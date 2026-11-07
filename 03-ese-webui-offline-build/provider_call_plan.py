"""Gate-bound adapter call plans + a blocked execution seam — offline-only.

This package composes the three existing artifacts — a validated provider
configuration, a request manifest (``provider_manifest``), and a per-provider/
per-run operator gate (``provider_gate``) — into the one declarative unit a
future live adapter would execute: a **call plan**. Nothing here executes.

- **Planning verifies without burning.** ``build_call_plan()`` re-verifies
  the gate's integrity (content hash, version, structure), its
  acknowledgment semantics, and its provider/run/action/configuration
  binding — but planning is read-only over the gate: there is no single-use
  ledger parameter and no state, so a planned gate remains fresh for the
  (still blocked) execution seam. Burning stays where it belongs, in
  ``provider_gate.execute_gated_action``. The non-burning verification is
  deliberately re-implemented here from the gate module's *public* identity
  contract (``gate_id`` = sha256 of the canonical body) rather than
  importing its private burning helpers.
- **Plans are declarative and redacted.** An API-mode plan carries exactly
  the manifest's existing request shape (method/route/host/structural auth
  posture), the provider/run/action/gate/manifest identifiers, and an
  explicit future-executor requirement. It contains no secret values, no
  resolved credentials, no cookies, no browser profiles, no subscription
  tokens, and no response bodies — ``public_view`` screens every emission.
- **Subscription plans make automation unrepresentable.** For the
  ChatGPT/Claude ``interactive_subscription`` modes the plan has no HTTP
  section, no request, and no transport surface at all; its executor is the
  human operator. There is nothing in the artifact an automated executor
  could act on.
- **The execution seam refuses everything.** ``execute_call_plan()`` raises
  :class:`provider_manifest.LiveProviderExecutionBlockedError` for every
  attempt — injected transport, injected resolver, ``live=True``, and even
  a fully valid, fully bound plan. No clocks, no randomness, no I/O of any
  kind; credential resolution and live calls remain a future, separately
  reviewed work package behind the operator gate.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from provider_gate import (
    GATE_VERSION,
    SUPPORTED_ACTIONS,
    GateStaleError,
    GateTamperError,
    GateValidationError,
    config_fingerprint,
)
from provider_health import to_json
from provider_manifest import (
    MANIFEST_VERSION,
    SUPPORTED_OPERATIONS,
    LiveProviderExecutionBlockedError,
    ManifestStaleError,
    ManifestTamperError,
    ManifestValidationError,
    build_health_request,
)
from providers import (
    ACCESS_INTERACTIVE_SUBSCRIPTION,
    ProviderConfigError,
    public_view,
    validate_provider_config,
)
from store import RUN_ID

PLAN_VERSION = 1

# The named executor an API-mode plan requires. It does not exist in this
# build; the name is a requirement statement, not a reference to code.
FUTURE_EXECUTOR = "future_live_adapter"

# Structural contracts of the upstream artifacts, restated here for the
# non-burning verification path. The hash recomputation below is the real
# integrity check; these key sets only give closed, typed shape errors.
_REQUIRED_GATE_KEYS = frozenset(
    {
        "gate_version",
        "provider",
        "run_id",
        "action",
        "config_sha256",
        "operator_ack",
        "single_use",
        "gate_id",
    }
)
_OPTIONAL_GATE_KEYS = frozenset({"execution_note"})

_REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "manifest_version",
        "provider",
        "access",
        "operation",
        "protocol_family",
        "credential",
        "requires",
        "config_sha256",
        "manifest_id",
    }
)
_OPTIONAL_MANIFEST_KEYS = frozenset({"http", "service", "manual_instruction"})

_REQUIRED_PLAN_KEYS = frozenset(
    {
        "plan_version",
        "provider",
        "access",
        "action",
        "run_id",
        "gate_id",
        "manifest_id",
        "config_sha256",
        "mode",
        "execution",
        "plan_id",
    }
)
_OPTIONAL_PLAN_KEYS = frozenset({"request", "service", "manual_instruction"})

_EXECUTION_BLOCKED_MESSAGE = (
    "live provider execution is blocked in this build; a call plan is a "
    "declaration only — executing it requires a future, separately reviewed "
    "live-adapter work package, explicit user authorization, and the "
    "operator gate it binds"
)


class CallPlanValidationError(ProviderConfigError):
    """Raised for a missing, malformed, or mis-bound call plan."""


class CallPlanTamperError(ProviderConfigError):
    """Raised when a plan's content does not match its plan_id."""


class CallPlanStaleError(ProviderConfigError):
    """Raised when a plan no longer matches the configuration it bound."""


def _sha256_of(body: dict[str, Any], error_cls: type[ProviderConfigError]) -> str:
    """Canonical content hash with a closed error contract.

    ``to_json`` raises typed :class:`providers.ProviderConfigError` for
    secret-shaped or non-mapping input, which propagates as-is. Values that
    are not canonical JSON at all (arbitrary objects, NaN) would otherwise
    surface as raw ``TypeError``/``ValueError`` from serialization —
    adversarial artifacts must fail closed and typed instead.
    """
    try:
        return hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        raise error_cls(
            "artifact contains values outside the canonical JSON contract"
        ) from None


def _require_semantic_ack(operator_ack: Any, provider_id: str, action: str) -> None:
    """Re-check acknowledgment semantics, not just the content hash.

    A hand-crafted gate could carry a self-consistent hash over a hollow
    acknowledgment; binding is only proven if the statement itself names
    the provider and the action.
    """
    if not isinstance(operator_ack, dict) or set(operator_ack) != {
        "confirmed",
        "statement",
    }:
        raise GateValidationError("operator_ack must be exactly {confirmed, statement}")
    if operator_ack["confirmed"] is not True:
        raise GateValidationError(
            "operator_ack.confirmed must be the literal boolean true"
        )
    statement = operator_ack["statement"]
    if not isinstance(statement, str) or not statement.strip():
        raise GateValidationError("operator_ack.statement must be a non-empty string")
    if not isinstance(provider_id, str) or not isinstance(action, str):
        raise GateValidationError(
            "gate provider and action must be strings before acknowledgment "
            "semantics can be checked"
        )
    if provider_id not in statement or action not in statement:
        raise GateValidationError(
            "operator_ack.statement must explicitly name both the provider "
            "id and the action being planned — blanket acknowledgments are "
            "not accepted"
        )


def verify_gate_binding(
    gate: Any,
    config: Any,
    *,
    run_id: Any,
    action: Any,
) -> dict[str, Any]:
    """Verify a gate's integrity and binding WITHOUT burning it.

    Read-only and stateless: no single-use ledger is consulted or updated,
    so a gate that passes here remains fresh for the (blocked) execution
    seam. Checks, fail closed and typed: requested run/action validity,
    gate structure, version, content hash, acknowledgment semantics, and
    the run/action/provider/configuration binding. Returns the validated
    configuration for reuse.
    """
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise GateValidationError(
            "run_id must match the run-id pattern (12 lowercase hex chars); "
            "a plan is always bound to exactly one run"
        )
    if action not in SUPPORTED_ACTIONS:
        raise GateValidationError(
            "unsupported action; supported: " + ", ".join(SUPPORTED_ACTIONS)
        )
    if not isinstance(gate, dict):
        raise GateValidationError(
            "a gate artifact from provider_gate.issue_gate is required; "
            "planning without a valid operator gate is not possible"
        )
    keys = set(gate)
    if not _REQUIRED_GATE_KEYS.issubset(keys) or keys - _REQUIRED_GATE_KEYS - _OPTIONAL_GATE_KEYS:
        raise GateValidationError("gate artifact is malformed: unexpected or missing fields")
    if gate["gate_version"] != GATE_VERSION:
        raise GateStaleError(
            "gate_version is not supported by this build; re-issue the gate"
        )
    body = {k: v for k, v in gate.items() if k != "gate_id"}
    if _sha256_of(body, GateValidationError) != gate["gate_id"]:
        raise GateTamperError(
            "gate content does not match its gate_id; the artifact was "
            "altered after issuance"
        )
    if gate["single_use"] is not True:
        raise GateValidationError("gates are single_use by contract")
    _require_semantic_ack(gate["operator_ack"], gate["provider"], gate["action"])
    if gate["run_id"] != run_id:
        raise GateValidationError(
            "gate is bound to a different run; gates are per-run, never global"
        )
    if gate["action"] != action:
        raise GateValidationError("gate authorizes a different action")
    validated = validate_provider_config(config)
    if gate["provider"] != validated["provider"]:
        raise GateValidationError(
            "gate is bound to a different provider; gates are per-provider, "
            "never global"
        )
    if gate["config_sha256"] != config_fingerprint(config):
        raise GateStaleError(
            "provider configuration has changed since the gate was issued; "
            "the gate is stale — re-issue it against the current configuration"
        )
    return validated


def _verify_manifest_binding(
    manifest: Any,
    validated: dict[str, Any],
    config: Any,
    *,
    action: str,
) -> dict[str, Any]:
    """Verify a manifest's integrity and its binding to config and action."""
    if not isinstance(manifest, dict):
        raise ManifestValidationError(
            "a manifest from provider_manifest.build_health_request is "
            "required; planning without a valid manifest is not possible"
        )
    keys = set(manifest)
    if not _REQUIRED_MANIFEST_KEYS.issubset(keys) or (
        keys - _REQUIRED_MANIFEST_KEYS - _OPTIONAL_MANIFEST_KEYS
    ):
        raise ManifestValidationError(
            "manifest is malformed: unexpected or missing fields"
        )
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ManifestStaleError(
            "manifest_version is not supported by this build; rebuild the manifest"
        )
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    if _sha256_of(body, ManifestValidationError) != manifest["manifest_id"]:
        raise ManifestTamperError(
            "manifest content does not match its manifest_id; the manifest "
            "was altered after it was built"
        )
    if manifest["operation"] not in SUPPORTED_OPERATIONS:
        raise ManifestValidationError("manifest names an unsupported operation")
    if manifest["operation"] != action:
        raise ManifestValidationError(
            "manifest operation does not match the gated action; a plan "
            "binds exactly one action end to end"
        )
    if manifest["provider"] != validated["provider"]:
        raise ManifestValidationError(
            "manifest is bound to a different provider than the supplied "
            "configuration"
        )
    if manifest["access"] != validated["access"]:
        raise ManifestValidationError(
            "manifest access mode does not match the provider registry"
        )
    # Cross-field consistency. The manifest_id hash proves the artifact was
    # not altered after it was built, but a hand-crafted manifest can carry
    # a self-consistent hash over an inconsistent body — reject that shape
    # outright rather than silently dropping fields downstream.
    if validated["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION:
        if (
            manifest["protocol_family"] != "manual_session"
            or "http" in manifest
            or "service" not in manifest
            or "manual_instruction" not in manifest
            or manifest["service"] != validated["service"]
        ):
            raise ManifestValidationError(
                "subscription manifests are manual_session only: no http "
                "section, with the configured service and its manual "
                "instruction — an automated request is unrepresentable"
            )
    else:
        if (
            manifest["protocol_family"] != "http"
            or "http" not in manifest
            or "service" in manifest
            or "manual_instruction" in manifest
        ):
            raise ManifestValidationError(
                "API manifests must carry exactly the http request shape "
                "and no manual-session fields"
            )
    if manifest["config_sha256"] != config_fingerprint(config):
        raise ManifestStaleError(
            "provider configuration has changed since the manifest was "
            "built; rebuild the manifest against the current configuration"
        )
    # Final semantic guard: manifests are deterministic functions of the
    # configuration, so any genuine manifest for this config is EQUAL to a
    # fresh rebuild. A hash-self-consistent forgery that injects foreign
    # fields (e.g. a cookie inside the http block) survives every check
    # above — it cannot survive this one.
    if manifest != build_health_request(config):
        raise ManifestValidationError(
            "manifest does not match its canonical rebuild from the "
            "configuration; altered or foreign request fields are rejected"
        )
    return manifest


def build_call_plan(
    config: Any,
    manifest: Any,
    gate: Any,
    *,
    run_id: Any,
    action: Any,
) -> dict[str, Any]:
    """Build the declarative, gate-bound call plan for one provider action.

    Pure and deterministic: equal inputs yield byte-identical plans, no
    input is mutated, and no state is kept. The gate is verified but NOT
    burned — see :func:`verify_gate_binding`. The result is a screened
    copy; it carries identifiers and request *shape* only, never secret
    values, resolved credentials, cookies, browser profiles, subscription
    tokens, or response bodies.
    """
    validated = verify_gate_binding(gate, config, run_id=run_id, action=action)
    manifest = _verify_manifest_binding(manifest, validated, config, action=action)

    body: dict[str, Any] = {
        "plan_version": PLAN_VERSION,
        "provider": validated["provider"],
        "access": validated["access"],
        "action": action,
        "run_id": run_id,
        "gate_id": gate["gate_id"],
        "manifest_id": manifest["manifest_id"],
        "config_sha256": gate["config_sha256"],
    }
    if validated["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION:
        body["mode"] = "manual_operator"
        body["service"] = manifest["service"]
        body["manual_instruction"] = manifest["manual_instruction"]
        body["execution"] = {
            "executor": "human_operator",
            "operator_gate_required": True,
            "executable_in_this_build": False,
            "note": (
                "execution is the human operator using their own "
                "subscription session; automated execution is "
                "unrepresentable — this plan contains no request and "
                "accepts no transport"
            ),
        }
    else:
        body["mode"] = "api_http"
        body["request"] = copy.deepcopy(manifest["http"])
        body["execution"] = {
            "executor": FUTURE_EXECUTOR,
            "operator_gate_required": True,
            "executable_in_this_build": False,
            "note": (
                "a future, separately reviewed live-adapter work package is "
                "required to execute this plan; it must re-verify and burn "
                "the bound operator gate and may resolve the credential "
                "reference only at that reviewed execution edge"
            ),
        }
    plan = dict(body)
    plan["plan_id"] = _sha256_of(body, CallPlanValidationError)
    return public_view(plan)


def _require_valid_plan_structure(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise CallPlanValidationError(
            "a call plan from build_call_plan is required; execution "
            "without a valid plan is not possible"
        )
    keys = set(plan)
    if not _REQUIRED_PLAN_KEYS.issubset(keys) or keys - _REQUIRED_PLAN_KEYS - _OPTIONAL_PLAN_KEYS:
        raise CallPlanValidationError(
            "call plan is malformed: unexpected or missing fields"
        )
    if plan["plan_version"] != PLAN_VERSION:
        raise CallPlanStaleError(
            "plan_version is not supported by this build; rebuild the plan"
        )
    body = {k: v for k, v in plan.items() if k != "plan_id"}
    if _sha256_of(body, CallPlanValidationError) != plan["plan_id"]:
        raise CallPlanTamperError(
            "plan content does not match its plan_id; the plan was altered "
            "after it was built"
        )
    # Mode/request consistency, for hand-crafted but self-consistent plans:
    # a manual plan must have no request surface at all.
    if plan["mode"] == "api_http":
        if "request" not in plan:
            raise CallPlanValidationError("api_http plans must carry a request shape")
    elif plan["mode"] == "manual_operator":
        if "request" in plan:
            raise CallPlanValidationError(
                "manual_operator plans cannot carry a request; automated "
                "execution of a subscription session is unrepresentable"
            )
    else:
        raise CallPlanValidationError("call plan names an unknown mode")
    return plan


def verify_call_plan_binding(plan: Any, config: Any, gate: Any) -> dict[str, Any]:
    """Verify plan integrity and canonical binding without consuming a gate.

    This is the reusable structural half of the execution edge. It performs
    the same canonical rebuild as :func:`execute_call_plan`, but returns the
    verified plan so a separately authorized executor can burn the gate at
    its actual execution boundary.
    """
    plan = _require_valid_plan_structure(plan)
    if plan["config_sha256"] != config_fingerprint(config):
        raise CallPlanStaleError(
            "provider configuration has changed since the plan was built; "
            "rebuild the plan against the current configuration"
        )
    validated = validate_provider_config(config)
    if plan["provider"] != validated["provider"]:
        raise CallPlanValidationError(
            "plan is bound to a different provider than the supplied "
            "configuration"
        )
    verify_gate_binding(gate, config, run_id=plan["run_id"], action=plan["action"])
    canonical = build_call_plan(
        config,
        build_health_request(config),
        gate,
        run_id=plan["run_id"],
        action=plan["action"],
    )
    if plan != canonical:
        raise CallPlanValidationError(
            "plan does not match its canonical rebuild from the "
            "configuration and gate; altered request fields, execution "
            "flags, or identifiers are rejected"
        )
    return plan


def execute_call_plan(
    plan: Any,
    config: Any,
    gate: Any,
    *,
    resolver: Any = None,
    transport: Any = None,
    live: Any = False,
) -> None:
    """Verify a plan against its configuration and gate, then refuse to run.

    The seam requires the operator gate itself — a plan is gate-BOUND, so
    presenting the plan without the gate it binds is not an execution
    attempt at all. Fail-closed order: injected transport → injected
    resolver → live request → plan structure/version/tamper/mode →
    plan/config fingerprint → full non-burning gate verification →
    **canonical-rebuild equality**: the plan must EQUAL
    ``build_call_plan(config, build_health_request(config), gate, ...)``,
    so a hash-self-consistent forgery (foreign request fields, flipped
    execution flags, forged gate/manifest identifiers) is rejected as
    invalid rather than "verified then blocked". Every attempt — including
    a fully valid, fully bound plan — terminates in
    :class:`provider_manifest.LiveProviderExecutionBlockedError`.

    Stateless and non-burning: this seam never executes anything, so it
    never consumes the gate — burn-on-attempt stays the contract of
    ``provider_gate.execute_gated_action``, the seam a future adapter must
    still pass. Repeated attempts refuse identically. No network,
    filesystem, environment, keychain, browser, cookie, subprocess, clock,
    or randomness machinery exists here.
    """
    if transport is not None:
        raise LiveProviderExecutionBlockedError(
            "a transport was injected, but " + _EXECUTION_BLOCKED_MESSAGE
        )
    if resolver is not None:
        raise LiveProviderExecutionBlockedError(
            "a credential resolver was injected, but " + _EXECUTION_BLOCKED_MESSAGE
        )
    if live is not False:
        raise LiveProviderExecutionBlockedError(
            "a live request was made, but " + _EXECUTION_BLOCKED_MESSAGE
        )
    verify_call_plan_binding(plan, config, gate)
    raise LiveProviderExecutionBlockedError(_EXECUTION_BLOCKED_MESSAGE)
