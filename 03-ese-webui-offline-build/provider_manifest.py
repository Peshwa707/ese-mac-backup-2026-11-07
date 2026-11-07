"""Provider request manifests + blocked resolver/transport seams — offline.

This package prepares — and only prepares — the shapes a future, separately
reviewed live adapter would need. Three separated concerns:

- **Manifests are declarations.** ``build_health_request()`` emits a
  canonical, deterministic description of what a health check would involve
  per provider: HTTP method/route/host and structural auth posture for API
  modes, or a manual-session instruction for user-owned ChatGPT/Claude
  subscription modes — where an automated request is *unrepresentable*
  (there is no HTTP section at all), not merely forbidden. Credential
  information is reference metadata only; auth posture is structure
  (``{"scheme": "bearer"}``), never a header-value template. Manifests bind
  the validated configuration's sha256 fingerprint (shared with the
  operator gate) and carry a content-derived ``manifest_id``, so tamper and
  staleness are detectable by recomputation. Routes and hosts are
  *declarative targets*; verifying them against the real services belongs
  to the live-adapter work package.
- **Authority stays with the operator gate** (``provider_gate``), which
  remains required for any future live call. Manifests carry no consumable
  authority: execution here is stateless, and every attempt is refused.
- **Resolution and transport are typed refusals.** ``CredentialResolver``
  and ``Transport`` are abstract seams. The only offline implementation,
  ``OfflineCredentialResolver``, validates the *reference* and then always
  raises :class:`CredentialResolutionBlockedError` — it cannot read
  environment values, keychains, cookies, browser profiles, or
  subscription sessions, and it never returns a secret.
  ``execute_manifest()`` rejects any injected transport or resolver and any
  live request with :class:`LiveProviderExecutionBlockedError`; a fully
  valid manifest still cannot cause I/O. No clocks, no randomness.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any

from provider_gate import config_fingerprint
from provider_health import to_json
from providers import (
    ACCESS_API_KEY,
    ACCESS_INTERACTIVE_SUBSCRIPTION,
    ACCESS_LOCAL_NO_AUTH,
    ProviderConfigError,
    provider_registry,
    public_view,
    validate_provider_config,
)

MANIFEST_VERSION = 1
SUPPORTED_OPERATIONS: tuple[str, ...] = ("health_check",)

# Declarative HTTP targets for API-mode health checks. These are the routes a
# future live adapter would call; they are metadata here and are never
# contacted by this package. Auth posture is structural — no value templates.
_HTTP_TARGETS: dict[str, dict[str, Any]] = {
    "openai_api": {"method": "GET", "route": "/v1/models", "host": "api.openai.com"},
    "anthropic_api": {"method": "GET", "route": "/v1/models", "host": "api.anthropic.com"},
    "ollama_local": {"method": "GET", "route": "/api/tags", "host": "127.0.0.1:11434"},
    "ollama_cloud": {"method": "GET", "route": "/api/tags", "host": "ollama.com"},
    "moonshot_kimi_api": {"method": "GET", "route": "/v1/models", "host": "api.moonshot.ai"},
    "deepseek_api": {"method": "GET", "route": "/v1/models", "host": "api.deepseek.com"},
}

_SERVICE_LABELS = {"chatgpt": "ChatGPT", "claude": "Claude"}

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


class ManifestValidationError(ProviderConfigError):
    """Raised for a missing, malformed, or unsupported manifest request."""


class ManifestTamperError(ProviderConfigError):
    """Raised when a manifest's content does not match its manifest_id."""


class ManifestStaleError(ProviderConfigError):
    """Raised when a manifest no longer matches the configuration it bound."""


class CredentialResolutionBlockedError(ProviderConfigError):
    """Raised by the offline resolver: credential resolution is disabled."""


class LiveProviderExecutionBlockedError(ProviderConfigError):
    """Raised by the execution seam: live provider execution is blocked.

    This is the deliberate terminal state of every execution attempt in
    this package, valid manifest or not — live calls remain blocked until a
    future, separately reviewed adapter work package, which also remains
    subject to the operator gate.
    """


class CredentialResolver(ABC):
    """Typed seam for future credential resolution at the execution edge.

    Implementations accept validated credential *references* only. No
    implementation in this package can produce a secret.
    """

    @abstractmethod
    def resolve(self, credential_ref: Any) -> None:
        """Resolve a credential reference (future seam)."""


class OfflineCredentialResolver(CredentialResolver):
    """The only resolver in this build: validates the reference, then refuses.

    Never reads environment values, keychains, cookies, browser profiles,
    or subscription sessions — there is no code path that could.
    """

    def resolve(self, credential_ref: Any) -> None:
        if not isinstance(credential_ref, dict):
            raise ProviderConfigError(
                "credential reference must be an object naming an "
                "environment variable or keychain item"
            )
        # Reuse the registry's full reference validation (grammar, forbidden
        # keys, secret screen) by validating it inside a scratch config.
        validate_provider_config(
            {"provider": "openai_api", "credential_ref": credential_ref}
        )
        raise CredentialResolutionBlockedError(
            "credential resolution is disabled in this build; references "
            "are held as names only and no secret is ever read or returned"
        )


class Transport(ABC):
    """Typed seam for a future live transport. Nothing here implements it."""

    @abstractmethod
    def send(self, manifest: Any) -> None:
        """Send a manifest-described request (future seam)."""


def _credential_block(validated: dict[str, Any]) -> dict[str, Any]:
    registry = provider_registry()
    default_env_var = registry[validated["provider"]]["default_env_var"]
    access = validated["access"]
    if access == ACCESS_LOCAL_NO_AUTH:
        posture = "none_required"
    elif access == ACCESS_INTERACTIVE_SUBSCRIPTION:
        posture = "user_owned_session"
    elif validated["credential_ref"] is not None:
        posture = "reference_configured"
    else:
        posture = "unconfigured"
    return {
        "posture": posture,
        "ref": validated["credential_ref"],
        "default_env_var": default_env_var,
    }


def build_request(config: Any, *, operation: Any) -> dict[str, Any]:
    """Build the canonical offline manifest for one provider operation.

    Fails closed on invalid configurations (including ``live_enabled: true``
    and secret-shaped input, via the registry) and unsupported operations.
    The result is a screened deep copy; the input is never mutated.
    """
    if operation not in SUPPORTED_OPERATIONS:
        raise ManifestValidationError(
            "unsupported manifest operation; supported: "
            + ", ".join(SUPPORTED_OPERATIONS)
        )
    validated = validate_provider_config(config)
    provider_id = validated["provider"]
    access = validated["access"]

    body: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "provider": provider_id,
        "access": access,
        "operation": operation,
        "credential": _credential_block(validated),
        "config_sha256": config_fingerprint(config),
    }
    if access == ACCESS_INTERACTIVE_SUBSCRIPTION:
        service = validated["service"]
        label = _SERVICE_LABELS[service]
        body["protocol_family"] = "manual_session"
        body["service"] = service
        body["manual_instruction"] = (
            f"the operator opens their own {label} subscription session and "
            "performs the health check manually; the WebUI never automates, "
            "scripts, logs into, or reads that session"
        )
        body["requires"] = {"network": False, "manual_operator": True}
    else:
        target = dict(_HTTP_TARGETS[provider_id])
        if access == ACCESS_API_KEY:
            target["auth"] = {"scheme": "bearer", "header": "Authorization"}
        else:
            target["auth"] = {"scheme": "none"}
        body["protocol_family"] = "http"
        body["http"] = target
        body["requires"] = {"network": True, "manual_operator": False}

    manifest = dict(body)
    manifest["manifest_id"] = hashlib.sha256(
        to_json(body).encode("utf-8")
    ).hexdigest()
    return public_view(manifest)


def build_health_request(config: Any) -> dict[str, Any]:
    """Build the canonical offline health-check manifest for a provider."""
    return build_request(config, operation="health_check")


def _require_valid_manifest_structure(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ManifestValidationError(
            "a manifest from build_health_request is required; execution "
            "without a valid manifest is not possible"
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
            "manifest_version is not supported by this build; rebuild the "
            "manifest"
        )
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    if hashlib.sha256(to_json(body).encode("utf-8")).hexdigest() != manifest["manifest_id"]:
        raise ManifestTamperError(
            "manifest content does not match its manifest_id; the manifest "
            "was altered after it was built"
        )
    if manifest["operation"] not in SUPPORTED_OPERATIONS:
        raise ManifestValidationError("manifest names an unsupported operation")
    return manifest


def execute_manifest(
    manifest: Any,
    config: Any,
    *,
    resolver: Any = None,
    transport: Any = None,
    live: Any = False,
) -> None:
    """Verify a manifest against its configuration, then refuse to execute.

    Fail-closed order: injected transport → injected resolver → live
    request → manifest structure/version/tamper → provider binding → config
    fingerprint. A manifest that passes everything still terminates in
    :class:`LiveProviderExecutionBlockedError` — this package prepares live
    adapters; it does not authorize or perform network access. Stateless:
    every repeated attempt is refused identically.
    """
    if transport is not None:
        raise LiveProviderExecutionBlockedError(
            "a transport was injected, but live provider execution is "
            "blocked in this build; no transport is accepted"
        )
    if resolver is not None:
        raise LiveProviderExecutionBlockedError(
            "a credential resolver was injected, but live provider "
            "execution is blocked in this build; no resolver is accepted"
        )
    if live is not False:
        raise LiveProviderExecutionBlockedError(
            "a live request was made, but live provider execution is "
            "blocked in this build pending the future adapter work package "
            "and the operator gate"
        )
    manifest = _require_valid_manifest_structure(manifest)
    validated = validate_provider_config(config)
    if manifest["provider"] != validated["provider"]:
        raise ManifestValidationError(
            "manifest is bound to a different provider than the supplied "
            "configuration"
        )
    if manifest["config_sha256"] != config_fingerprint(config):
        raise ManifestStaleError(
            "provider configuration has changed since the manifest was "
            "built; rebuild the manifest against the current configuration"
        )
    raise LiveProviderExecutionBlockedError(
        "manifest verified, but live provider execution is blocked in this "
        "build — this package prepares future, separately reviewed live "
        "adapters and does not authorize network access"
    )
