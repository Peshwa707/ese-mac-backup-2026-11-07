"""User-owned manual article-access records with identity verification.

This module records an operator's observation after they use an authorized
browser, library, publisher, or document-delivery workflow. It never opens a
URL, reads cookies, automates authentication, downloads content, or assesses
license/reuse rights.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any
from urllib.parse import urlsplit

from full_text_access import (
    ConnectorInputError,
    _IDENTITY_FIELDS,
    _identity,
    compare_identity,
    connector_registry,
)
from provider_health import to_json
from providers import ProviderConfigError, public_view

MANUAL_RECORD_VERSION = 1
_ACCESS_STATES = frozenset(
    {"full_text_observed", "abstract_only", "login_required", "not_found", "ill_requested"}
)
_ROUTE_IDS = frozenset(entry["connector_id"] for entry in connector_registry())


class ManualRecordError(ConnectorInputError):
    """Raised for an unsafe or internally inconsistent manual access record."""


def _validate_source_url(value: Any, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ManualRecordError(
                "full_text_observed requires an HTTPS source_url for provenance"
            )
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManualRecordError("source_url must be a non-empty HTTPS URL")
    if len(value) > 2048 or any(ord(char) < 0x20 or char.isspace() for char in value):
        raise ManualRecordError("source_url is outside the bounded URL contract")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ManualRecordError(
            "source_url must use HTTPS and cannot contain embedded credentials"
        )
    if parsed.query or parsed.fragment:
        raise ManualRecordError("source_url cannot contain a query or fragment")
    return value


def _require_complete_identity(citation: dict[str, Any], candidate: dict[str, Any]) -> None:
    expected = _identity(citation)
    missing = [
        field
        for field in _IDENTITY_FIELDS
        if expected[field] is not None
        and (
            candidate.get(field) is None
            or (
                isinstance(candidate.get(field), str)
                and not candidate[field].strip()
            )
        )
    ]
    if missing:
        raise ManualRecordError(
            "full_text_observed requires every available citation identity field"
        )


def record_manual_access(
    citation: Any,
    *,
    route_id: str,
    access_state: str,
    candidate: Any,
    source_url: str | None = None,
    operator_note: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic manual access record without external I/O."""
    if route_id not in _ROUTE_IDS:
        raise ManualRecordError("route_id is not in the connector catalog")
    if access_state not in _ACCESS_STATES:
        raise ManualRecordError("access_state is outside the manual record contract")
    if not isinstance(candidate, dict):
        raise ManualRecordError("candidate identity must be an object")
    try:
        public_view({"citation": citation, "candidate": candidate})
    except ProviderConfigError:
        raise ManualRecordError(
            "manual access input contains secret-shaped material and was not retained"
        ) from None
    if operator_note is not None and (
        not isinstance(operator_note, str) or len(operator_note) > 2000
    ):
        raise ManualRecordError("operator_note must be a bounded string when present")
    if operator_note is not None:
        try:
            public_view({"operator_note": operator_note})
        except ProviderConfigError:
            raise ManualRecordError(
                "operator_note contains secret-shaped material and was not retained"
            ) from None
    source = _validate_source_url(source_url, required=access_state == "full_text_observed")
    try:
        comparison = compare_identity(citation, candidate)
    except ConnectorInputError:
        raise
    if access_state == "full_text_observed":
        safe_citation = copy.deepcopy(citation)
        if not isinstance(safe_citation, dict):
            raise ManualRecordError("citation must be an object")
        _require_complete_identity(safe_citation, candidate)
        if not comparison["match"]:
            raise ManualRecordError("retrieved article identity does not match the citation")
    body = {
        "record_version": MANUAL_RECORD_VERSION,
        "original_citation": copy.deepcopy(citation),
        "route_id": route_id,
        "access_state": access_state,
        "candidate_identity": copy.deepcopy(candidate),
        "identity_check": comparison,
        "source_url": source,
        "operator_note": operator_note,
        "retrieval_method": "manual_operator_record",
        "license_status": "not_assessed",
        "verified_full_text": access_state == "full_text_observed" and comparison["match"],
        "warnings": ["This record does not establish license or reuse rights."],
    }
    try:
        public_view(body)
        record_id = hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()
    except (ProviderConfigError, TypeError, ValueError):
        raise ManualRecordError("manual access record could not be canonicalized") from None
    return public_view({**body, "record_id": record_id})
