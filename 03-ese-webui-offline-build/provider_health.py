"""Offline-only provider health/check boundary atop the provider registry.

An honest offline "health check" is a *plan that refuses to lie*: it reports
only what is knowable from configuration alone and explicitly names the live
steps it will not run. Concretely:

- ``check_provider()`` validates a configuration through
  :func:`providers.validate_provider_config`, classifies its credential
  posture purely from the configuration object (never reading environment
  values, the keychain, cookies, browser profiles, or subscription
  sessions), and returns a deterministic, redacted plan/result whose
  ``live_connectivity_probe`` step is always ``blocked_offline`` pending the
  operator-authorized live-test gate described in ``docs/PROVIDERS.md``.
- A ``reference_configured`` classification means exactly that a credential
  *reference* (a name) is present. It never asserts that a secret exists or
  is usable — no offline path can know that, so no offline result claims it.
- Any attempt to request live execution (``live=<non-False>``) or to inject
  a network transport (``transport=<non-None>``) fails closed with a typed
  error. There is no code path here that performs I/O of any kind.
- Results carry no timestamps and no randomness; ``to_json()`` serializes
  them canonically (sorted keys) after re-screening through
  :func:`providers.public_view`, so serialization is byte-deterministic and
  cannot emit secret-shaped material even for a tampered result.
"""

from __future__ import annotations

import json
from typing import Any

from providers import (
    ACCESS_API_KEY,
    ACCESS_INTERACTIVE_SUBSCRIPTION,
    ACCESS_LOCAL_NO_AUTH,
    PROVIDER_IDS,
    ProviderConfigError,
    provider_registry,
    public_view,
    validate_provider_config,
)

_LIVE_BLOCKED_REASON = (
    "live provider access is disabled in this build; enabling it requires "
    "the explicit operator-authorized live-test gate (docs/PROVIDERS.md)"
)


class LiveExecutionBlockedError(ProviderConfigError):
    """Raised when a caller requests live execution of a provider check."""


class TransportInjectionError(ProviderConfigError):
    """Raised when a caller injects a network transport into a check."""


def _classify(access: str, credential_ref: dict[str, str] | None) -> tuple[str, str]:
    """Classify credential posture from configuration alone.

    Returns ``(classification, detail)``. The detail wording is part of the
    honesty contract: it must never over-claim (no "healthy", no "verified",
    no assertion that a secret exists).
    """
    if access == ACCESS_LOCAL_NO_AUTH:
        return (
            "no_auth_local",
            "local no-auth mode; this provider has no credential surface",
        )
    if access == ACCESS_INTERACTIVE_SUBSCRIPTION:
        return (
            "user_owned_session",
            "the human operator drives their own subscription session; no "
            "cookie, session, or profile material is stored, read, or "
            "automated",
        )
    if credential_ref is not None:
        return (
            "reference_configured",
            "a credential reference (a name, not a value) is configured; "
            "this does not confirm that any secret exists or is usable",
        )
    return ("unconfigured", "no credential reference is configured")


def _build_result(
    provider_id: str,
    access: str,
    classification: str,
    detail: str,
    credential_ref: dict[str, str] | None,
    service: str | None,
    validate_status: str,
    validate_detail: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": provider_id,
        "access": access,
        "live": False,
        "classification": classification,
        "credential_ref": credential_ref,
        "steps": [
            {
                "step": "validate_config",
                "status": validate_status,
                "detail": validate_detail,
            },
            {
                "step": "classify_credential_reference",
                "status": "ok",
                "outcome": classification,
                "detail": detail,
            },
            {
                "step": "live_connectivity_probe",
                "status": "blocked_offline",
                "reason": _LIVE_BLOCKED_REASON,
            },
        ],
    }
    if service is not None:
        result["service"] = service
    return result


def check_provider(
    config: Any,
    *,
    live: Any = False,
    transport: Any = None,
) -> dict[str, Any]:
    """Return the deterministic, redacted offline check result for a config.

    Fails closed, in order: a live-execution request raises
    :class:`LiveExecutionBlockedError`; an injected transport raises
    :class:`TransportInjectionError`; an invalid configuration propagates
    :class:`providers.ProviderConfigError`. The returned result is a fresh,
    screened copy — the input is never mutated and no state is retained.
    """
    if live is not False:
        raise LiveExecutionBlockedError(
            "live execution was requested but is disabled; " + _LIVE_BLOCKED_REASON
        )
    if transport is not None:
        raise TransportInjectionError(
            "a network transport was injected; this boundary performs no "
            "I/O and accepts no transport, pending the operator-authorized "
            "live-test gate"
        )
    validated = validate_provider_config(config)
    classification, detail = _classify(
        validated["access"], validated["credential_ref"]
    )
    result = _build_result(
        provider_id=validated["provider"],
        access=validated["access"],
        classification=classification,
        detail=detail,
        credential_ref=validated["credential_ref"],
        service=validated.get("service"),
        validate_status="ok",
        validate_detail="configuration validated by the provider registry",
    )
    return public_view(result)


def plan_registry_checks() -> list[dict[str, Any]]:
    """Return the default (unconfigured) check plan for every provider.

    One deterministic entry per registered provider, sorted by id. Entries
    are templates built from the registry specification, not validated
    operator configs; ``interactive_subscription`` therefore reports
    ``service_required`` on its validation step instead of pretending a
    service was chosen.
    """
    registry = provider_registry()
    plan: list[dict[str, Any]] = []
    for provider_id in sorted(PROVIDER_IDS):
        access = registry[provider_id]["access"]
        classification, detail = _classify(access, None)
        if access == ACCESS_INTERACTIVE_SUBSCRIPTION:
            validate_status = "service_required"
            validate_detail = (
                "pass service 'chatgpt' or 'claude' to check_provider for a "
                "validated result"
            )
        else:
            validate_status = "not_validated"
            validate_detail = (
                "default unconfigured template; pass a real configuration "
                "to check_provider for a validated result"
            )
        plan.append(
            _build_result(
                provider_id=provider_id,
                access=access,
                classification=classification,
                detail=detail,
                credential_ref=None,
                service=None,
                validate_status=validate_status,
                validate_detail=validate_detail,
            )
        )
    return [public_view(entry) for entry in plan]


def to_json(value: Any) -> str:
    """Canonically serialize a check result (or list of results).

    Screens through :func:`providers.public_view` first, so a tampered
    structure carrying secret-shaped material raises instead of being
    emitted. Output is sorted-key JSON with fixed separators — byte-identical
    for equal inputs, with no timestamps or randomness.
    """
    if isinstance(value, dict):
        screened: Any = public_view(value)
    elif isinstance(value, list):
        screened = [public_view(entry) for entry in value]
    else:
        raise ProviderConfigError(
            "to_json expects a check result object or a list of them"
        )
    return json.dumps(
        screened,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
