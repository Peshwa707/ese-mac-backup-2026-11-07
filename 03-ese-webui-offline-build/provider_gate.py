"""Per-provider/per-run operator live-test gate — offline-only, fail closed.

This module lets an operator *record* an explicit, bounded authorization for
exactly one provider, one run, and one action — and proves that even a fully
valid authorization cannot reach the network in this package:

- ``issue_gate()`` produces a deterministic gate artifact bound to the
  sha256 fingerprint of the validated provider configuration's canonical
  redacted JSON. The gate's own identity (``gate_id``) is the sha256 of its
  canonical body, so any tamper is detectable by recomputation and equal
  inputs always yield byte-identical artifacts.
- The acknowledgment must be ``{"confirmed": True, "statement": ...}`` where
  the statement NAMES both the provider id and the action. A blanket "yes"
  cannot authorize anything, and no wildcard/global gate is expressible —
  the structure requires one provider id and one run id.
- **Clock-free by design.** There are no timestamps and no randomness:
  staleness is structural (the bound configuration hash no longer matches,
  or the gate version is unknown), and replay protection is a caller-owned
  single-use ledger that every execution attempt must present. A gate burns
  on its first execution attempt.
- ``execute_gated_action()`` remains the legacy offline refusal seam: it
  verifies everything, burns the gate, and raises
  :class:`LiveExecutionNotImplementedError`. The separately reviewed
  ``verify_and_burn_gate()`` helper exposes only the validated burn operation
  to an execution edge that supplies its own explicit resolver and transport.
  This module itself performs no I/O and never resolves credentials or reads
  environment values, keychains, cookies, browser profiles, or subscription
  sessions.
- Interactive ChatGPT/Claude subscription gates carry an explicit note that
  any future execution is manual and operator-driven, never automated.
"""

from __future__ import annotations

import hashlib
from typing import Any

from provider_health import TransportInjectionError, to_json
from providers import ProviderConfigError, validate_provider_config, public_view
from store import RUN_ID

GATE_VERSION = 1
SUPPORTED_ACTIONS: tuple[str, ...] = ("health_check",)

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

_INTERACTIVE_NOTE = (
    "interactive subscription sessions are manual and operator-driven; "
    "no automation, cookie/session access, or login is ever performed"
)


class GateValidationError(ProviderConfigError):
    """Raised for a missing, malformed, or mis-bound gate or acknowledgment."""


class GateStaleError(ProviderConfigError):
    """Raised when a gate no longer matches the configuration it authorized."""


class GateTamperError(ProviderConfigError):
    """Raised when a gate's content hash does not match its gate_id."""


class GateReusedError(ProviderConfigError):
    """Raised when a single-use gate is presented a second time."""


class LiveExecutionNotImplementedError(ProviderConfigError):
    """Raised by the legacy offline execution seam: live execution is refused.

    The newer ``provider_executor`` contract is separate and requires
    explicit caller-injected runtime capabilities.
    """


def config_fingerprint(config: Any) -> str:
    """Return the sha256 hex digest of the validated config's canonical JSON.

    Validation and redaction come first, so the fingerprint covers exactly
    the normalized, reference-only form — never secret material, which the
    registry refuses to hold at all.
    """
    return hashlib.sha256(
        to_json(validate_provider_config(config)).encode("utf-8")
    ).hexdigest()


def _require_valid_run_id(run_id: Any) -> str:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise GateValidationError(
            "run_id must match the run-id pattern (12 lowercase hex chars); "
            "a gate is always bound to exactly one run"
        )
    return run_id


def _require_supported_action(action: Any) -> str:
    if action not in SUPPORTED_ACTIONS:
        raise GateValidationError(
            "unsupported gate action; supported: " + ", ".join(SUPPORTED_ACTIONS)
        )
    return action


def _require_valid_ack(operator_ack: Any, provider_id: str, action: str) -> dict[str, Any]:
    if not isinstance(operator_ack, dict) or set(operator_ack) != {
        "confirmed",
        "statement",
    }:
        raise GateValidationError(
            "operator_ack must be exactly {confirmed, statement}"
        )
    if operator_ack["confirmed"] is not True:
        raise GateValidationError(
            "operator_ack.confirmed must be the literal boolean true"
        )
    statement = operator_ack["statement"]
    if not isinstance(statement, str) or not statement.strip():
        raise GateValidationError("operator_ack.statement must be a non-empty string")
    if provider_id not in statement or action not in statement:
        raise GateValidationError(
            "operator_ack.statement must explicitly name both the provider "
            "id and the action being authorized — blanket acknowledgments "
            "are not accepted"
        )
    return {"confirmed": True, "statement": statement}


def _gate_id_for(body: dict[str, Any]) -> str:
    return hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()


def issue_gate(
    config: Any,
    *,
    run_id: Any,
    action: Any,
    operator_ack: Any,
) -> dict[str, Any]:
    """Issue a deterministic, per-provider/per-run, single-use gate artifact.

    Fails closed on malformed run ids, unsupported actions, missing or
    non-explicit acknowledgments, invalid configurations, and secret-shaped
    material anywhere in the artifact. The result is a screened copy; the
    input configuration is never mutated.
    """
    validated = validate_provider_config(config)
    provider_id = validated["provider"]
    run_id = _require_valid_run_id(run_id)
    action = _require_supported_action(action)
    ack = _require_valid_ack(operator_ack, provider_id, action)

    body: dict[str, Any] = {
        "gate_version": GATE_VERSION,
        "provider": provider_id,
        "run_id": run_id,
        "action": action,
        "config_sha256": config_fingerprint(config),
        "operator_ack": ack,
        "single_use": True,
    }
    if validated["access"] == "interactive_subscription":
        body["execution_note"] = _INTERACTIVE_NOTE
    gate = dict(body)
    gate["gate_id"] = _gate_id_for(body)
    return public_view(gate)


def _require_valid_gate_structure(gate: Any) -> dict[str, Any]:
    if not isinstance(gate, dict):
        raise GateValidationError(
            "a gate artifact from issue_gate is required; live execution "
            "without a valid gate is not possible"
        )
    keys = set(gate)
    if not _REQUIRED_GATE_KEYS.issubset(keys) or keys - _REQUIRED_GATE_KEYS - _OPTIONAL_GATE_KEYS:
        raise GateValidationError(
            "gate artifact is malformed: unexpected or missing fields"
        )
    if gate["gate_version"] != GATE_VERSION:
        raise GateStaleError(
            "gate_version is not supported by this build; re-issue the gate"
        )
    body = {k: v for k, v in gate.items() if k != "gate_id"}
    if _gate_id_for(body) != gate["gate_id"]:
        raise GateTamperError(
            "gate content does not match its gate_id; the artifact was "
            "altered after issuance"
        )
    if gate["single_use"] is not True:
        raise GateValidationError("gates are single_use by contract")
    return public_view(gate)


def verify_and_burn_gate(
    gate: Any,
    config: Any,
    *,
    run_id: Any,
    action: Any,
    used_gates: set,
):
    """Verify a gate completely and burn it before a real attempt.

    Verification order is closed and typed: gate structure/version/tamper →
    acknowledgment → run and
    action binding → provider and configuration-fingerprint binding →
    single-use ledger. A gate that passes everything is recorded in
    ``used_gates`` (burn-on-attempt) before the caller resolves credentials
    or invokes a transport. The returned artifact is a screened copy.
    """
    if not isinstance(used_gates, set):
        raise GateValidationError(
            "used_gates must be the caller-owned single-use ledger (a set)"
        )
    gate = _require_valid_gate_structure(gate)
    _require_valid_ack(gate["operator_ack"], gate["provider"], gate["action"])
    _require_valid_run_id(gate["run_id"])
    _require_supported_action(gate["action"])
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
            "the gate is stale — re-issue it against the current "
            "configuration"
        )
    if gate["gate_id"] in used_gates:
        raise GateReusedError(
            "this single-use gate was already presented; re-issue a fresh "
            "gate for another attempt"
        )
    used_gates.add(gate["gate_id"])
    return public_view(gate)


def execute_gated_action(
    gate: Any,
    config: Any,
    *,
    run_id: Any,
    action: Any,
    used_gates: set,
    transport: Any = None,
) -> None:
    """Verify a gate, burn it, then refuse the legacy offline seam.

    The optional transport remains a deliberate injection refusal for this
    legacy function. A separately reviewed execution edge may call
    :func:`verify_and_burn_gate` and then use its own explicit resolver and
    transport contract.
    """
    if transport is not None:
        raise TransportInjectionError(
            "a network transport was injected; the gated execution seam "
            "performs no I/O in this build"
        )
    verify_and_burn_gate(
        gate,
        config,
        run_id=run_id,
        action=action,
        used_gates=used_gates,
    )
    raise LiveExecutionNotImplementedError(
        "gate verified and burned, but live execution is not implemented in "
        "this build — live provider access remains blocked until a future, "
        "separately reviewed work package"
    )
