"""Reference-only configuration for institutional full-text routes.

The profile records how a later user-owned connector may be reached. It never
resolves environment variables or keychain items, opens a browser, validates
an entitlement, or contacts a database. A profile is safe to save, review,
and pass to a later explicitly authorized connector runtime.
"""

from __future__ import annotations

import copy
import hashlib
from urllib.parse import urlsplit
from typing import Any

from provider_health import to_json
from providers import ProviderConfigError, public_view, validate_provider_config

ACCESS_CONFIG_VERSION = 1

_SPECS: tuple[dict[str, Any], ...] = (
    {
        "connector_id": "pubmed_central",
        "label": "PubMed Central open-access subset",
        "modes": ["public_no_auth"],
        "requires": "public_open_access_check",
    },
    {
        "connector_id": "crossref",
        "label": "Crossref DOI metadata",
        "modes": ["public_no_auth"],
        "requires": "public_metadata_check",
    },
    {
        "connector_id": "google_scholar",
        "label": "Google Scholar discovery",
        "modes": ["manual_browser"],
        "requires": "user_browser_session",
    },
    {
        "connector_id": "scopus",
        "label": "Elsevier Scopus",
        "modes": ["api_key_reference", "manual_browser"],
        "requires": "institution_or_approved_api_access",
    },
    {
        "connector_id": "embase",
        "label": "Elsevier Embase",
        "modes": ["api_key_reference", "manual_browser"],
        "requires": "institution_or_approved_api_access",
    },
    {
        "connector_id": "ezproxy",
        "label": "Institutional EZproxy route",
        "modes": ["proxy_url", "manual_browser"],
        "requires": "institution_approved_proxy_and_user_login",
    },
    {
        "connector_id": "publisher",
        "label": "Publisher DOI landing page",
        "modes": ["manual_browser"],
        "requires": "user_owned_publisher_or_institutional_session",
    },
    {
        "connector_id": "interlibrary_loan",
        "label": "Interlibrary loan or document delivery",
        "modes": ["manual_library_request"],
        "requires": "human_library_workflow",
    },
    {
        "connector_id": "manual_browser",
        "label": "Manual browser session",
        "modes": ["manual_browser"],
        "requires": "human_operator",
    },
)

_SPEC_BY_ID = {spec["connector_id"]: spec for spec in _SPECS}
_ENTRY_KEYS = frozenset(
    {
        "connector_id",
        "enabled",
        "mode",
        "credential_ref",
        "proxy_base_url",
        "institution_label",
    }
)


class AccessConfigError(ProviderConfigError):
    """Raised for malformed or secret-bearing access profiles."""


def access_config_registry() -> list[dict[str, Any]]:
    """Return a caller-owned copy of the supported configuration modes."""
    return copy.deepcopy(list(_SPECS))


def _validate_proxy_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccessConfigError("proxy_base_url must be a non-empty HTTPS URL")
    if any(ord(char) < 0x20 or char.isspace() for char in value):
        raise AccessConfigError("proxy_base_url contains whitespace or control characters")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AccessConfigError(
            "proxy_base_url must be HTTPS and cannot contain embedded credentials"
        )
    if parsed.query or parsed.fragment:
        raise AccessConfigError("proxy_base_url cannot contain a query or fragment")
    return value.rstrip("/")


def _validate_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise AccessConfigError("each access connector entry must be an object")
    unknown = set(entry) - _ENTRY_KEYS
    if unknown:
        raise AccessConfigError("access connector entry contains unsupported fields")
    connector_id = entry.get("connector_id")
    if not isinstance(connector_id, str) or connector_id not in _SPEC_BY_ID:
        raise AccessConfigError("access connector id is unsupported")
    spec = _SPEC_BY_ID[connector_id]
    enabled = entry.get("enabled")
    if not isinstance(enabled, bool):
        raise AccessConfigError("access connector enabled must be a boolean")
    mode = entry.get("mode")
    if mode not in spec["modes"]:
        raise AccessConfigError("access connector mode is not supported for this connector")

    institution_label = entry.get("institution_label")
    if institution_label is not None and (
        not isinstance(institution_label, str) or not institution_label.strip()
    ):
        raise AccessConfigError("institution_label must be a non-empty string when present")

    normalized: dict[str, Any] = {
        "connector_id": connector_id,
        "enabled": enabled,
        "mode": mode,
    }
    if institution_label is not None:
        normalized["institution_label"] = institution_label.strip()

    ref = entry.get("credential_ref")
    if mode == "api_key_reference":
        if not enabled:
            if ref is not None:
                normalized["credential_ref"] = _validated_ref(ref)
        else:
            if ref is None:
                raise AccessConfigError(
                    "an enabled API connector requires a credential reference, not a value"
                )
            normalized["credential_ref"] = _validated_ref(ref)
    elif ref is not None:
        raise AccessConfigError("this connector mode does not accept a credential reference")

    proxy_url = entry.get("proxy_base_url")
    if mode == "proxy_url":
        if not enabled:
            if proxy_url is not None:
                normalized["proxy_base_url"] = _validate_proxy_url(proxy_url)
        else:
            if proxy_url is None:
                raise AccessConfigError("an enabled proxy route requires proxy_base_url")
            normalized["proxy_base_url"] = _validate_proxy_url(proxy_url)
    elif proxy_url is not None:
        raise AccessConfigError("this connector mode does not accept proxy_base_url")

    return normalized


def _validated_ref(ref: Any) -> dict[str, str]:
    try:
        validated = validate_provider_config(
            {"provider": "openai_api", "credential_ref": ref}
        )
    except ProviderConfigError:
        raise AccessConfigError(
            "credential_ref is invalid; only a named env or keychain reference is accepted"
        ) from None
    result = validated.get("credential_ref")
    if not isinstance(result, dict):
        raise AccessConfigError("credential_ref is invalid")
    return result


def validate_access_config(config: Any) -> dict[str, Any]:
    """Validate and canonically order a reference-only institutional profile."""
    if not isinstance(config, dict) or set(config) != {"connectors"}:
        raise AccessConfigError("access profile must contain exactly connectors")
    try:
        public_view({"access_profile": config})
    except ProviderConfigError:
        raise AccessConfigError(
            "access profile contains secret-shaped material and was not retained"
        ) from None
    entries = config["connectors"]
    if not isinstance(entries, list):
        raise AccessConfigError("access profile connectors must be a list")
    normalized_by_id = {}
    for entry in entries:
        normalized = _validate_entry(entry)
        connector_id = normalized["connector_id"]
        if connector_id in normalized_by_id:
            raise AccessConfigError("access profile cannot contain duplicate connectors")
        normalized_by_id[connector_id] = normalized
    ordered = [
        normalized_by_id[spec["connector_id"]]
        for spec in _SPECS
        if spec["connector_id"] in normalized_by_id
    ]
    body = {"config_version": ACCESS_CONFIG_VERSION, "connectors": ordered}
    return {**body, "config_id": hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()}
