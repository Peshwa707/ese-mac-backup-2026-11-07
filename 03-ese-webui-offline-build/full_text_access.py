"""Offline full-text access routing for lawful, user-authorized retrieval.

This module builds a deterministic routing plan from a citation. It does not
search, log in, resolve institutional entitlements, follow publisher links,
read browser sessions, download articles, or claim that a route produced full
text. A later connector may execute one route only behind a separate
operator-approved network and identity-verification gate.
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from provider_health import to_json
from providers import ProviderConfigError, public_view
from full_text_config import (
    ACCESS_CONFIG_VERSION,
    AccessConfigError,
    validate_access_config,
)

ACCESS_PLAN_VERSION = 1

_CITATION_KEYS = frozenset({"doi", "pmid", "title", "journal", "year"})
_IDENTITY_FIELDS = ("doi", "pmid", "title", "journal", "year")

# The order is deliberately conservative: identity/open-access discovery
# first, then licensed indexes and entitlement routes, then manual fallbacks.
_CONNECTORS: tuple[dict[str, Any], ...] = (
    {
        "connector_id": "pubmed_central",
        "label": "PubMed Central open-access subset",
        "access_state": "open_access_candidate",
        "auth_posture": "none",
        "full_text_capability": "licensed_open_access_subset_only",
        "manual_action": "Check the returned OA record and license before reuse.",
    },
    {
        "connector_id": "crossref",
        "label": "Crossref metadata and DOI resolution",
        "access_state": "metadata_only",
        "auth_posture": "none",
        "full_text_capability": "metadata_or_publisher_link_only",
        "manual_action": "Use the DOI metadata to verify identity and follow the publisher link.",
    },
    {
        "connector_id": "google_scholar",
        "label": "Google Scholar discovery",
        "access_state": "discovery_manual",
        "auth_posture": "user_browser_session",
        "full_text_capability": "candidate_pdf_html_or_library_link",
        "manual_action": "Search manually; inspect PDF/HTML, All versions, or the library link.",
    },
    {
        "connector_id": "scopus",
        "label": "Elsevier Scopus",
        "access_state": "metadata_or_entitlement_link",
        "auth_posture": "institution_or_api_authorization",
        "full_text_capability": "abstract_and_links_not_full_text_guarantee",
        "manual_action": "Use an authorized Scopus account or approved API; verify the publisher target separately.",
    },
    {
        "connector_id": "embase",
        "label": "Elsevier Embase",
        "access_state": "metadata_or_entitlement_link",
        "auth_posture": "institution_or_api_authorization",
        "full_text_capability": "abstract_and_links_not_full_text_guarantee",
        "manual_action": "Use an authorized Embase account or approved API; verify the publisher target separately.",
    },
    {
        "connector_id": "ezproxy",
        "label": "Institutional EZproxy route",
        "access_state": "institutional_login_required",
        "auth_posture": "user_owned_institutional_session",
        "full_text_capability": "lawful_entitlement_route_only",
        "manual_action": "Open the institution-approved proxy URL and complete login yourself.",
    },
    {
        "connector_id": "publisher",
        "label": "Publisher DOI landing page",
        "access_state": "publisher_access_check",
        "auth_posture": "user_owned_publisher_or_institutional_session",
        "full_text_capability": "publisher_full_text_if_entitled",
        "manual_action": "Open the DOI target, authenticate through the permitted route, and verify article identity.",
    },
    {
        "connector_id": "interlibrary_loan",
        "label": "Interlibrary loan or document delivery",
        "access_state": "library_request",
        "auth_posture": "human_library_workflow",
        "full_text_capability": "library_supplied_copy_if_fulfilled",
        "manual_action": "Submit a lawful library request; retain the library response and license conditions.",
    },
    {
        "connector_id": "manual_browser",
        "label": "Manual browser session",
        "access_state": "manual_user_session",
        "auth_posture": "human_operator_only",
        "full_text_capability": "user_verified_copy_only",
        "manual_action": "The operator may use an authorized browser session; the engine does not automate login or 2FA.",
    },
)


class ConnectorInputError(ProviderConfigError):
    """Raised when a routing input is malformed or contains secret material."""


def connector_registry() -> list[dict[str, Any]]:
    """Return the immutable connector catalog as a caller-owned copy."""
    return copy.deepcopy(list(_CONNECTORS))


def _safe_citation(citation: Any) -> dict[str, Any]:
    if not isinstance(citation, dict):
        raise ConnectorInputError("citation must be an object")
    try:
        public_view({"citation": citation})
    except ProviderConfigError:
        raise ConnectorInputError(
            "citation contains secret-shaped material and was not retained"
        ) from None
    unknown = set(citation) - _CITATION_KEYS
    if unknown:
        raise ConnectorInputError("citation contains unsupported fields")
    result = copy.deepcopy(citation)
    for field in _IDENTITY_FIELDS:
        if field in result and result[field] is not None:
            if field == "year":
                if isinstance(result[field], bool) or not isinstance(result[field], int):
                    raise ConnectorInputError("citation.year must be an integer or null")
            elif not isinstance(result[field], str):
                raise ConnectorInputError(f"citation.{field} must be a string or null")
    return result


def _normalize_doi(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    normalized = re.sub(r"^https?://(dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized)
    if not normalized or any(char.isspace() for char in normalized):
        raise ConnectorInputError("citation.doi is not a usable DOI identifier")
    return normalized


def _normalize_pmid(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if not re.fullmatch(r"\d+", normalized):
        raise ConnectorInputError("citation.pmid must contain digits only")
    return normalized


def _identity(citation: dict[str, Any]) -> dict[str, Any]:
    title = citation.get("title")
    journal = citation.get("journal")
    return {
        "doi": _normalize_doi(citation.get("doi")),
        "pmid": _normalize_pmid(citation.get("pmid")),
        "title": title.strip() if isinstance(title, str) and title.strip() else None,
        "journal": journal.strip() if isinstance(journal, str) and journal.strip() else None,
        "year": citation.get("year"),
    }


def _hash_plan(body: dict[str, Any]) -> str:
    return hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()


def _validated_access_profile(access_config: Any) -> dict[str, Any]:
    if not isinstance(access_config, dict):
        raise ConnectorInputError("access_config must be a validated access profile")
    allowed_shapes = (
        {"connectors"},
        {"config_version", "connectors", "config_id"},
    )
    if set(access_config) not in allowed_shapes:
        raise ConnectorInputError("access_config has unsupported top-level fields")
    if "config_version" in access_config and access_config["config_version"] != ACCESS_CONFIG_VERSION:
        raise ConnectorInputError("access_config uses an unsupported version")
    raw = {"connectors": access_config.get("connectors")}
    try:
        profile = validate_access_config(raw)
    except AccessConfigError:
        raise ConnectorInputError("access_config is malformed or stale") from None
    supplied_id = access_config.get("config_id")
    if supplied_id is not None and supplied_id != profile["config_id"]:
        raise ConnectorInputError("access_config config_id does not match its contents")
    return profile


def build_access_plan(
    citation: Any,
    *,
    enabled_connectors: list[str] | None = None,
    access_config: Any = None,
) -> dict[str, Any]:
    """Build a deterministic, offline route plan for one citation."""
    safe_citation = _safe_citation(citation)
    identity = _identity(safe_citation)
    profile = None if access_config is None else _validated_access_profile(access_config)
    configured_by_id = {
        entry["connector_id"]: entry for entry in (profile or {}).get("connectors", [])
    }
    allowed = {entry["connector_id"] for entry in _CONNECTORS}
    if enabled_connectors is None:
        selected = set(allowed)
    else:
        if not isinstance(enabled_connectors, list) or any(
            not isinstance(item, str) for item in enabled_connectors
        ):
            raise ConnectorInputError("enabled_connectors must be a list of connector ids")
        if len(set(enabled_connectors)) != len(enabled_connectors):
            raise ConnectorInputError("enabled_connectors must not contain duplicates")
        if not enabled_connectors:
            raise ConnectorInputError("enabled_connectors must contain at least one connector")
        unknown = set(enabled_connectors) - allowed
        if unknown:
            raise ConnectorInputError("enabled_connectors contains an unsupported connector")
        selected = set(enabled_connectors)

    routes = []
    for entry in _CONNECTORS:
        if entry["connector_id"] not in selected:
            continue
        route = copy.deepcopy(entry)
        configured = configured_by_id.get(entry["connector_id"])
        if configured is None:
            if entry["connector_id"] in {"pubmed_central", "crossref"}:
                state = "no_profile_needed"
            elif entry["connector_id"] in {
                "google_scholar",
                "publisher",
                "interlibrary_loan",
                "manual_browser",
            }:
                state = "manual_operator_route"
            else:
                state = "not_configured"
        elif configured["enabled"] is False:
            state = "disabled"
        elif configured["mode"] == "api_key_reference":
            state = "configured_reference_only"
        elif configured["mode"] == "proxy_url":
            state = "configured_manual_route"
        elif configured["mode"] == "public_no_auth":
            state = "configured_public_route"
        else:
            state = "configured_manual_route"
        route["configuration_state"] = state
        route["verified_full_text"] = False
        routes.append(route)
    warnings = []
    if identity["doi"] is None and identity["pmid"] is None:
        warnings.append("citation has no DOI or PMID")
    warnings.extend(
        [
            "a route or entitlement link does not prove full-text retrieval",
            "identity must be checked on any retrieved article before extraction",
        ]
    )
    body = {
        "access_version": ACCESS_PLAN_VERSION,
        "original_citation": safe_citation,
        "identity": identity,
        "routes": routes,
        "warnings": warnings,
        "access_config_id": None if profile is None else profile["config_id"],
    }
    return {**body, "plan_id": _hash_plan(body)}


def compare_identity(citation: Any, candidate: Any) -> dict[str, Any]:
    """Compare supplied identity fields without fetching or reading a file."""
    expected = _identity(_safe_citation(citation))
    if not isinstance(candidate, dict):
        raise ConnectorInputError("candidate identity must be an object")
    unknown = set(candidate) - set(_IDENTITY_FIELDS)
    if unknown:
        raise ConnectorInputError("candidate identity contains unsupported fields")
    mismatches = []
    for field in _IDENTITY_FIELDS:
        if field not in candidate or candidate[field] is None or expected[field] is None:
            continue
        value = candidate[field]
        if field == "doi":
            if not isinstance(value, str):
                raise ConnectorInputError("candidate.doi must be a string or null")
            value = _normalize_doi(value)
        elif field == "pmid":
            if not isinstance(value, str):
                raise ConnectorInputError("candidate.pmid must be a string or null")
            value = _normalize_pmid(value)
        elif field in {"title", "journal"} and isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        elif field in {"title", "journal"}:
            raise ConnectorInputError(f"candidate.{field} must be a string or null")
        elif field == "year":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConnectorInputError("candidate.year must be an integer or null")
        if value is None:
            continue
        if value != expected[field]:
            mismatches.append(field)
    return {"match": not mismatches, "mismatches": mismatches}
