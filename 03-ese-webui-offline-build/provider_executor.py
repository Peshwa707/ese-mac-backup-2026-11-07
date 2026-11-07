"""Explicit execution edge for one authorized provider health check.

This module is the first live-capable seam, but it has no default resolver or
transport. A caller must provide both, provide a fresh per-run gate, and
present a versioned fixture authorization. Codex tests use only in-memory
fixtures; no environment value or network transport is instantiated here.

The execution hook revalidates authorization, the canonical call plan, and
the gate on every invocation. It burns the caller-owned gate ledger before
credential resolution or transport invocation, and returns only the bounded
normalized response summary. It never returns, logs, or includes the request
headers or credential value in its result.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol
from urllib.error import HTTPError
import urllib.request

from provider_call_plan import verify_call_plan_binding
from provider_gate import verify_and_burn_gate
from provider_response import normalize_health_response
from providers import (
    ACCESS_API_KEY,
    ACCESS_INTERACTIVE_SUBSCRIPTION,
    ProviderConfigError,
    public_view,
    validate_provider_config,
)

EXECUTION_VERSION = 1
EXECUTION_FIXTURE_ID = "provider-health-v1"


class AuthorizationRequiredError(ProviderConfigError):
    """Raised when the explicit live execution authorization is absent."""


class CredentialResolutionError(ProviderConfigError):
    """Raised when a caller-supplied resolver cannot provide a credential."""


class TransportRequiredError(ProviderConfigError):
    """Raised when a caller-supplied transport is absent or malformed."""


class LiveExecutionError(ProviderConfigError):
    """Raised for a redacted resolver or transport failure."""


class ManualSubscriptionRequiredError(ProviderConfigError):
    """Raised because user-owned ChatGPT/Claude sessions stay manual."""


class CredentialResolver(Protocol):
    def resolve(self, credential_ref: dict[str, str]) -> str:
        """Resolve one validated reference at the execution edge."""


class Transport(Protocol):
    def send(self, request: dict[str, Any]) -> Any:
        """Send one prepared request and return a bounded response shape."""


class EnvironmentCredentialResolver:
    """Resolve one environment reference only when explicitly injected.

    The class is not constructed or used by the WebUI. It is a user-runtime
    capability: the caller chooses to inject it into the authorized execution
    function, and the returned value lives only for that call.
    """

    def resolve(self, credential_ref: dict[str, str]) -> str:
        try:
            validated = validate_provider_config(
                {"provider": "openai_api", "credential_ref": credential_ref}
            )
        except ProviderConfigError:
            raise CredentialResolutionError(
                "credential reference is invalid; no secret was read"
            ) from None
        reference = validated["credential_ref"]
        if not isinstance(reference, dict) or reference.get("kind") != "env":
            raise CredentialResolutionError(
                "this resolver accepts environment references only; keychain "
                "resolution requires a separate user-owned runtime"
            )
        value = os.environ.get(reference["name"])
        if not isinstance(value, str) or not value:
            raise CredentialResolutionError(
                "the named environment credential is unavailable; no secret "
                "value was returned"
            )
        return value


_ALLOWED_TARGET_SCHEMES = {
    "api.openai.com": "https",
    "api.anthropic.com": "https",
    "ollama.com": "https",
    "api.moonshot.ai": "https",
    "api.deepseek.com": "https",
    "127.0.0.1:11434": "http",
}

# Defense in depth for callers that use the transport directly: the
# canonical call-plan check remains authoritative, but the transport also
# refuses a provider/host/route combination that is not this fixture's exact
# target. Keeping this closed prevents a future caller from widening a plan
# accidentally between verification and send.
_ALLOWED_TARGETS = {
    "openai_api": ("api.openai.com", "/v1/models"),
    "anthropic_api": ("api.anthropic.com", "/v1/models"),
    "ollama_local": ("127.0.0.1:11434", "/api/tags"),
    "ollama_cloud": ("ollama.com", "/api/tags"),
    "moonshot_kimi_api": ("api.moonshot.ai", "/v1/models"),
    "deepseek_api": ("api.deepseek.com", "/v1/models"),
}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn redirects into typed bounded responses instead of reissuing auth."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect refused", headers, fp)


class UrllibTransport:
    """Bounded standard-library transport for a caller-authorized runtime.

    It accepts only the fixed hosts and GET request shape emitted by the
    canonical health-check plan. It returns only ``status`` and a bounded JSON
    body; it never logs or includes request headers in a result.
    """

    def __init__(self, *, timeout_seconds: float = 10, max_response_bytes: int = 2_000_000):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 60
        ):
            raise TransportRequiredError("timeout_seconds must be between 0 and 60")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 0 < max_response_bytes <= 8_000_000
        ):
            raise TransportRequiredError(
                "max_response_bytes must be a positive bounded integer"
            )
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        )

    def _read_json(self, handle: Any) -> dict[str, Any]:
        raw = handle.read(self.max_response_bytes + 1)
        if len(raw) > self.max_response_bytes:
            raise LiveExecutionError("provider response exceeded the bounded size")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LiveExecutionError(
                "provider response was not bounded UTF-8 JSON"
            ) from None
        if not isinstance(body, dict):
            raise LiveExecutionError("provider response body must be a JSON object")
        return body

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "execution_version",
            "fixture_id",
            "provider",
            "operation",
            "method",
            "route",
            "host",
            "headers",
        }:
            raise TransportRequiredError("request is outside the versioned execution contract")
        if request["execution_version"] != EXECUTION_VERSION or request["fixture_id"] != EXECUTION_FIXTURE_ID:
            raise TransportRequiredError("request names an unsupported execution fixture")
        if request["operation"] != "health_check" or request["method"] != "GET":
            raise TransportRequiredError("only the bounded GET health-check is supported")
        host = request["host"]
        route = request["route"]
        headers = request["headers"]
        if (
            request["provider"] not in _ALLOWED_TARGETS
            or not isinstance(host, str)
            or host not in _ALLOWED_TARGET_SCHEMES
            or not isinstance(route, str)
            or not route.startswith("/")
            or any(ord(char) < 0x20 or char.isspace() for char in route)
            or not isinstance(headers, dict)
            or set(headers) - {"Authorization"}
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items())
            or _ALLOWED_TARGETS.get(request["provider"]) != (host, route)
            or any("\r" in value or "\n" in value for value in headers.values() if isinstance(value, str))
            or (host == "127.0.0.1:11434" and headers)
        ):
            raise TransportRequiredError("request target or headers are outside the allowlist")
        scheme = _ALLOWED_TARGET_SCHEMES[host]
        target = f"{scheme}://{host}{route}"
        try:
            outbound = urllib.request.Request(target, headers=dict(headers), method="GET")
            with self._opener.open(outbound, timeout=self.timeout_seconds) as response:
                return {"status": int(response.status), "body": self._read_json(response)}
        except HTTPError as exc:
            return {"status": int(exc.code), "body": self._read_json(exc)}
        except LiveExecutionError:
            raise
        except Exception:
            raise LiveExecutionError(
                "provider transport failed; response body was not returned"
            ) from None


def _require_authorization(
    authorization: Any, *, provider: str, action: str
) -> dict[str, Any]:
    if not isinstance(authorization, dict) or set(authorization) != {
        "confirmed",
        "fixture_id",
        "statement",
    }:
        raise AuthorizationRequiredError(
            "execution authorization must be exactly confirmed, fixture_id, "
            "and statement"
        )
    if authorization["confirmed"] is not True:
        raise AuthorizationRequiredError(
            "execution authorization.confirmed must be the literal boolean true"
        )
    if authorization["fixture_id"] != EXECUTION_FIXTURE_ID:
        raise AuthorizationRequiredError(
            "execution authorization names an unsupported fixture; re-issue "
            "authorization for the versioned health-check fixture"
        )
    statement = authorization["statement"]
    if not isinstance(statement, str) or not statement.strip():
        raise AuthorizationRequiredError(
            "execution authorization.statement must be non-empty"
        )
    expected_statement = (
        f"I authorize {action} for provider {provider} using fixture "
        f"{EXECUTION_FIXTURE_ID}."
    )
    if statement != expected_statement:
        raise AuthorizationRequiredError(
            "execution authorization.statement must exactly name the provider, "
            "action, and versioned fixture"
        )
    try:
        public_view({"authorization": authorization})
    except ProviderConfigError:
        raise AuthorizationRequiredError(
            "execution authorization carries unsupported secret-shaped material"
        ) from None
    return dict(authorization)


def _require_runtime(resolver: Any, transport: Any, access: str) -> None:
    if transport is None or not callable(getattr(transport, "send", None)):
        raise TransportRequiredError(
            "an explicit caller-supplied transport is required; this module "
            "does not create one"
        )
    if access == ACCESS_API_KEY and (
        resolver is None or not callable(getattr(resolver, "resolve", None))
    ):
        raise CredentialResolutionError(
            "an explicit caller-supplied credential resolver is required; "
            "this module does not read credentials"
        )


def _request_from_plan(
    plan: dict[str, Any], validated: dict[str, Any], secret: str | None
) -> dict[str, Any]:
    """Build and validate a request descriptor inside the execution hook."""
    if plan.get("execution", {}).get("executable_in_this_build") is not False:
        raise AuthorizationRequiredError(
            "call plan is not marked as the non-executable provider-health "
            "fixture contract"
        )
    request = plan.get("request")
    if not isinstance(request, dict):
        raise AuthorizationRequiredError(
            "API execution requires the canonical HTTP request shape"
        )
    if set(request) != {"method", "route", "host", "auth"}:
        raise AuthorizationRequiredError(
            "call plan request shape is outside the versioned health-check contract"
        )
    auth = request["auth"]
    if not isinstance(auth, dict):
        raise AuthorizationRequiredError("call plan auth posture is malformed")
    headers: dict[str, str] = {}
    if auth == {"scheme": "none"}:
        if secret is not None:
            raise CredentialResolutionError(
                "a no-auth provider received a credential at the execution edge"
            )
    elif auth == {"scheme": "bearer", "header": "Authorization"}:
        if not isinstance(secret, str) or not secret:
            raise CredentialResolutionError(
                "the credential resolver returned no usable credential"
            )
        headers["Authorization"] = f"Bearer {secret}"
    else:
        raise AuthorizationRequiredError(
            "call plan auth posture is outside the versioned health-check contract"
        )
    return {
        "execution_version": EXECUTION_VERSION,
        "fixture_id": EXECUTION_FIXTURE_ID,
        "provider": validated["provider"],
        "operation": plan["action"],
        "method": request["method"],
        "route": request["route"],
        "host": request["host"],
        "headers": headers,
    }


def execute_authorized_health_check(
    plan: Any,
    config: Any,
    gate: Any,
    *,
    authorization: Any,
    used_gates: set,
    resolver: CredentialResolver | None,
    transport: Transport | None,
) -> dict[str, Any]:
    """Execute exactly one explicitly authorized API health-check fixture.

    The function itself is the authorization boundary: direct construction of
    a plan or authorization object cannot bypass the per-call validation. The
    function is not wired to the offline WebUI endpoint in this increment, so
    Codex cannot trigger a live call accidentally.
    """
    verified_plan = verify_call_plan_binding(plan, config, gate)
    validated = validate_provider_config(config)
    provider = validated["provider"]
    action = verified_plan["action"]
    _require_authorization(authorization, provider=provider, action=action)
    if validated["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION:
        raise ManualSubscriptionRequiredError(
            "user-owned ChatGPT/Claude subscription sessions remain manual; "
            "use the manual operator record instead of an automated transport"
        )
    _require_runtime(resolver, transport, validated["access"])

    # The gate is burned only after all structural and explicit user checks,
    # but before a resolver or transport can be attempted.
    verify_and_burn_gate(
        gate,
        config,
        run_id=verified_plan["run_id"],
        action=action,
        used_gates=used_gates,
    )

    secret: str | None = None
    if validated["access"] == ACCESS_API_KEY:
        reference = validated["credential_ref"]
        if not isinstance(reference, dict):
            raise CredentialResolutionError(
                "an API provider requires a validated credential reference"
            )
        try:
            secret = resolver.resolve(reference)  # type: ignore[union-attr]
        except Exception:
            raise LiveExecutionError(
                "credential resolution failed; secret material was not returned"
            ) from None

    request = _request_from_plan(verified_plan, validated, secret)
    try:
        response = transport.send(request)  # type: ignore[union-attr]
    except Exception:
        raise LiveExecutionError(
            "provider transport failed; the response was not normalized"
        ) from None

    try:
        normalized = normalize_health_response(config, response)
    except ProviderConfigError:
        raise
    result = {
        "execution_version": EXECUTION_VERSION,
        "fixture_id": EXECUTION_FIXTURE_ID,
        "provider": provider,
        "operation": action,
        "result": normalized,
    }
    return public_view(result)
