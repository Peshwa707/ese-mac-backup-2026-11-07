"""Explicit, caller-injected metadata/entitlement probe seam.

This module prepares and optionally executes one bounded Scopus or Embase
metadata probe. It is intentionally not wired to the WebUI: a caller must
provide an exact fixture authorization, a fresh single-use attempt ledger, a
credential resolver, and a transport. The probe never claims full text and
returns only a response status/hash, not the response body.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Protocol
from urllib.parse import quote

from full_text_access import ConnectorInputError, build_access_plan
from full_text_config import validate_access_config
from provider_health import to_json
from providers import ProviderConfigError, public_view

FULL_TEXT_PROBE_VERSION = 1
FULL_TEXT_PROBE_FIXTURE_ID = "full-text-metadata-probe-v1"
_OPERATION = "metadata_or_entitlement_probe"
_MAX_RESPONSE_BYTES = 2_000_000


class AccessProbeAuthorizationError(ProviderConfigError):
    """Raised when the explicit probe authorization is absent or forged."""


class AccessProbeExecutionError(ProviderConfigError):
    """Raised for missing runtime capabilities or redacted probe failures."""


class ManualAccessRequiredError(ProviderConfigError):
    """Raised when a route must remain a human-owned browser workflow."""


class AccessCredentialResolver(Protocol):
    def resolve(self, credential_ref: dict[str, str]) -> str:
        """Resolve one caller-owned reference at the execution edge."""


class AccessTransport(Protocol):
    def send(self, request: dict[str, Any]) -> Any:
        """Send one caller-prepared request and return a bounded response."""


_SUPPORTED = {"scopus", "embase"}


def _hash_body(body: dict[str, Any]) -> str:
    return hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()


def _probe_body(plan: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(plan)
    body.pop("probe_id", None)
    return body


def _validated_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise AccessProbeExecutionError("a full-text probe plan object is required")
    if set(plan) != {
        "probe_version",
        "fixture_id",
        "connector_id",
        "operation",
        "identity",
        "request",
        "credential_ref",
        "config_id",
        "full_text_verified",
        "probe_id",
    }:
        raise AccessProbeExecutionError("probe plan shape is outside the versioned contract")
    if plan["probe_version"] != FULL_TEXT_PROBE_VERSION:
        raise AccessProbeExecutionError("probe plan version is unsupported")
    if plan["fixture_id"] != FULL_TEXT_PROBE_FIXTURE_ID:
        raise AccessProbeExecutionError("probe plan fixture is unsupported")
    if plan["connector_id"] not in _SUPPORTED:
        raise ManualAccessRequiredError("this connector has no automated probe in this build")
    if plan["operation"] != _OPERATION or plan["full_text_verified"] is not False:
        raise AccessProbeExecutionError("probe plan operation or claim ceiling is invalid")
    try:
        public_view({"probe": plan})
    except ProviderConfigError:
        raise AccessProbeExecutionError(
            "probe plan carries secret-shaped material"
        ) from None
    identity = plan["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "doi",
        "pmid",
        "title",
        "journal",
        "year",
    }:
        raise AccessProbeExecutionError("probe identity shape is invalid")
    if identity["doi"] is None and identity["pmid"] is None:
        raise AccessProbeExecutionError("probe identity has no persistent identifier")
    request = plan["request"]
    if not isinstance(request, dict) or set(request) != {
        "method",
        "host",
        "route",
        "params",
        "auth",
    }:
        raise AccessProbeExecutionError("probe request shape is invalid")
    if request["method"] != "GET" or request["host"] != "api.elsevier.com":
        raise AccessProbeExecutionError("probe target is outside the fixed Elsevier host")
    if request["auth"] != {
        "scheme": "api_key_reference",
        "header": "X-ELS-APIKey",
    }:
        raise AccessProbeExecutionError("probe authorization posture is invalid")
    if plan["connector_id"] == "scopus":
        expected_route = "/content/search/scopus"
        expected_query = (
            f"DOI({identity['doi']})"
            if identity["doi"] is not None
            else f"PMID({identity['pmid']})"
        )
        expected_params = {"query": expected_query, "httpAccept": "application/json"}
    else:
        if identity["doi"] is not None:
            expected_route = f"/content/embase/article/doi/{quote(identity['doi'], safe='')}"
        else:
            expected_route = f"/content/embase/article/pubmed_id/{identity['pmid']}"
        expected_params = {"httpAccept": "application/json"}
    if request["route"] != expected_route or request["params"] != expected_params:
        raise AccessProbeExecutionError("probe route is not bound to its identity")
    try:
        validate_access_config(
            {
                "connectors": [
                    {
                        "connector_id": plan["connector_id"],
                        "enabled": True,
                        "mode": "api_key_reference",
                        "credential_ref": plan["credential_ref"],
                    }
                ]
            }
        )
    except ProviderConfigError:
        raise AccessProbeExecutionError(
            "probe credential reference is invalid"
        ) from None
    if plan.get("probe_id") != _hash_body(_probe_body(plan)):
        raise AccessProbeExecutionError("probe plan content does not match its probe_id")
    return copy.deepcopy(plan)


def _require_authorization(authorization: Any, connector_id: str) -> None:
    if not isinstance(authorization, dict) or set(authorization) != {
        "confirmed",
        "fixture_id",
        "statement",
    }:
        raise AccessProbeAuthorizationError(
            "probe authorization must be exactly confirmed, fixture_id, and statement"
        )
    expected = (
        "I authorize metadata_or_entitlement_probe for provider "
        f"{connector_id} using fixture {FULL_TEXT_PROBE_FIXTURE_ID}."
    )
    if (
        authorization["confirmed"] is not True
        or authorization["fixture_id"] != FULL_TEXT_PROBE_FIXTURE_ID
        or authorization["statement"] != expected
    ):
        raise AccessProbeAuthorizationError(
            "probe authorization must exactly name the provider, operation, and fixture"
        )
    try:
        public_view({"authorization": authorization})
    except ProviderConfigError:
        raise AccessProbeAuthorizationError(
            "probe authorization carries unsupported secret-shaped material"
        ) from None


def build_probe_plan(citation: Any, access_config: Any) -> dict[str, Any]:
    """Build one deterministic metadata/entitlement probe descriptor."""
    try:
        route_plan = build_access_plan(citation, access_config=access_config)
    except ConnectorInputError:
        raise
    routes = {
        route["connector_id"]: route for route in route_plan["routes"]
    }
    profile_entries = access_config.get("connectors")
    if not isinstance(profile_entries, list):
        raise ConnectorInputError("access_config must contain a connectors list")
    configured_entries = [
        entry
        for entry in profile_entries
        if isinstance(entry, dict) and entry.get("connector_id") in _SUPPORTED
    ]
    if not configured_entries:
        if profile_entries:
            raise ManualAccessRequiredError(
                "the configured access profile contains manual routes only"
            )
        raise ConnectorInputError("a Scopus or Embase access profile entry is required")
    configured = [
        connector_id
        for connector_id in _SUPPORTED
        if routes.get(connector_id, {}).get("configuration_state")
        == "configured_reference_only"
    ]
    if len(configured) != 1:
        raise ManualAccessRequiredError(
            "select exactly one configured Scopus or Embase API route for a probe"
        )
    connector_id = configured[0]
    route = routes[connector_id]
    if route["configuration_state"] != "configured_reference_only":
        raise ManualAccessRequiredError(
            "this route is not configured as a reference-only API connector"
        )
    identity = route_plan["identity"]
    if identity["doi"] is None and identity["pmid"] is None:
        raise ConnectorInputError("a metadata probe requires a DOI or PMID")
    if connector_id == "scopus":
        query = (
            f"DOI({identity['doi']})"
            if identity["doi"] is not None
            else f"PMID({identity['pmid']})"
        )
        request = {
            "method": "GET",
            "host": "api.elsevier.com",
            "route": "/content/search/scopus",
            "params": {"query": query, "httpAccept": "application/json"},
            "auth": {"scheme": "api_key_reference", "header": "X-ELS-APIKey"},
        }
    else:
        if identity["doi"] is not None:
            route_path = f"/content/embase/article/doi/{quote(identity['doi'], safe='')}"
        else:
            route_path = f"/content/embase/article/pubmed_id/{identity['pmid']}"
        request = {
            "method": "GET",
            "host": "api.elsevier.com",
            "route": route_path,
            "params": {"httpAccept": "application/json"},
            "auth": {"scheme": "api_key_reference", "header": "X-ELS-APIKey"},
        }
    selected_entry = next(
        entry
        for entry in configured_entries
        if entry.get("connector_id") == connector_id
    )
    credential_ref = selected_entry.get("credential_ref")
    if not isinstance(credential_ref, dict):
        raise AccessProbeExecutionError(
            "configured API route has no reference-only credential"
        )
    body = {
        "probe_version": FULL_TEXT_PROBE_VERSION,
        "fixture_id": FULL_TEXT_PROBE_FIXTURE_ID,
        "connector_id": connector_id,
        "operation": _OPERATION,
        "identity": identity,
        "request": request,
        "credential_ref": credential_ref,
        "config_id": route_plan["access_config_id"],
        "full_text_verified": False,
    }
    return {**body, "probe_id": _hash_body(body)}


def _normalized_response(plan: dict[str, Any], response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or set(response) != {"status", "body"}:
        raise AccessProbeExecutionError("probe response must contain only status and body")
    try:
        public_view({"response": response})
        serialized = to_json(response)
    except (ProviderConfigError, TypeError, ValueError):
        raise AccessProbeExecutionError(
            "probe response was invalid or carried secret-shaped material"
        ) from None
    if len(serialized.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise AccessProbeExecutionError("probe response exceeded the bounded size")
    if isinstance(response["status"], bool) or not isinstance(response["status"], int):
        raise AccessProbeExecutionError("probe response status must be an integer")
    body = {
        "probe_version": FULL_TEXT_PROBE_VERSION,
        "fixture_id": FULL_TEXT_PROBE_FIXTURE_ID,
        "connector_id": plan["connector_id"],
        "operation": _OPERATION,
        "identity": plan["identity"],
        "status": response["status"],
        "response_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "full_text_verified": False,
    }
    return public_view(body)


def execute_authorized_probe(
    plan: Any,
    *,
    authorization: Any,
    used_attempts: set,
    resolver: AccessCredentialResolver | None,
    transport: AccessTransport | None,
) -> dict[str, Any]:
    """Execute one explicitly authorized, injected metadata probe."""
    verified = _validated_plan(plan)
    _require_authorization(authorization, verified["connector_id"])
    if not isinstance(used_attempts, set):
        raise AccessProbeExecutionError("used_attempts must be a caller-owned set")
    if resolver is None or not callable(getattr(resolver, "resolve", None)):
        raise AccessProbeExecutionError("an explicit credential resolver is required")
    if transport is None or not callable(getattr(transport, "send", None)):
        raise AccessProbeExecutionError("an explicit transport is required")
    if verified["probe_id"] in used_attempts:
        raise AccessProbeExecutionError("this probe is single-use")
    # Burn before any caller-owned secret resolution or network transport.
    used_attempts.add(verified["probe_id"])
    try:
        secret = resolver.resolve(verified["credential_ref"])
        if (
            not isinstance(secret, str)
            or not secret
            or any(ord(char) < 0x20 or char.isspace() for char in secret)
        ):
            raise RuntimeError("empty credential")
        request = copy.deepcopy(verified["request"])
        request["headers"] = {"X-ELS-APIKey": secret}
        response = transport.send(request)
    except Exception:
        raise AccessProbeExecutionError(
            "probe runtime failed; secret and response were not returned"
        ) from None
    return _normalized_response(verified, response)
