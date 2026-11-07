"""Bounded, offline canary specifications for one public literature connector,
one public trial-registry connector, and one LLM screening provider.

Every canary defined here is fixture-only: it describes the exact, minimal
request a future opt-in live smoke test would send for a *fixed, known*
identifier, and validates a *supplied* fixture response against a versioned
request schema, a bounded capability/access state, and an identity check.

Nothing in this module performs network I/O. `PubMedArticleCanary` and
`ClinicalTrialsRecordCanary` subclass `screen_prove.live_boundary.ConnectorBoundary`;
`SingleLlmScreeningCanary` subclasses `ProviderBoundary`. Their inherited
`fetch_live`/`execute_live` entry points still require
`SCREEN_PROVE_ALLOW_LIVE_SMOKE=1` (see `screen_prove.live_boundary`), and none
of them implement `_execute_request_live`, so even an authorized call raises
`NotImplementedError`: this module ships specifications, not a live
connector or provider implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .contracts import IntegrityError, ProtocolLock, TaskEnvelope, object_hash
from .live_boundary import ConnectorBoundary, LiveRequestDescription, ProviderBoundary
from .models import ScreenDecision

CANARY_SCHEMA_VERSION = "canary-request-v1"
PUBMED_CANARY_PMID = "39551604"
CLINICALTRIALS_CANARY_NCT_ID = "NCT00000000"
LLM_CANARY_RECORD_ID = "fixture-record-v1"
LLM_CANARY_TITLE = "Fixture screening record v1"


class AccessState(str, Enum):
    """Bounded retrieval/access states a canary response may report.

    An access state must never be silently upgraded by an adapter or an LLM;
    a fixture (or a future live response) must report exactly one of these
    values, and `validate_access_state` rejects anything else.
    """

    METADATA_ONLY = "metadata_only"
    ABSTRACT_AVAILABLE = "abstract_available"
    FULL_TEXT_AVAILABLE = "full_text_available"
    PAYWALLED = "paywalled"
    LOGIN_REQUIRED = "login_required"
    INSTITUTIONAL_ACCESS_REQUIRED = "institutional_access_required"
    UNRESOLVED = "unresolved"


def validate_access_state(value: str) -> AccessState:
    try:
        return AccessState(value)
    except ValueError as exc:
        raise IntegrityError(f"unknown canary access state: {value!r}") from exc


def verify_identity(expected: Mapping[str, str], reported: Mapping[str, Any]) -> None:
    """Verify a canary response describes the same record it was asked for.

    Only the identifier fields present in `expected` are compared; every one
    of them must also be present in `reported` with an exact match (DOI is
    compared case-insensitively). This is a bounded "same publication or
    record" identity check: it never infers or merges identity beyond an
    exact match on the fields the caller asked about.
    """
    for field, expected_value in expected.items():
        if field not in reported:
            raise IntegrityError(f"canary response is missing identifier field: {field}")
        reported_value = reported[field]
        normalize = (lambda value: str(value).strip().lower()) if field == "doi" else (lambda value: str(value).strip())
        if normalize(expected_value) != normalize(reported_value):
            raise IntegrityError(
                f"canary identity mismatch on {field}: requested {expected_value!r}, reported {reported_value!r}"
            )


@dataclass(frozen=True)
class CanaryRequestSchema:
    """A versioned, bounded description of what one canary is allowed to ask for."""

    version: str
    canary: str
    boundary: str  # "connector" or "provider"
    operation: str
    required_identifier_fields: tuple[str, ...]
    allowed_access_states: tuple[AccessState, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "canary": self.canary,
            "boundary": self.boundary,
            "operation": self.operation,
            "required_identifier_fields": list(self.required_identifier_fields),
            "allowed_access_states": [state.value for state in self.allowed_access_states],
        }


def build_task_envelope(
    lock: ProtocolLock,
    description: LiveRequestDescription,
    provider_or_source: str,
    model_or_target: str,
    response: Mapping[str, Any],
) -> TaskEnvelope:
    """Build a `TaskEnvelope` for one canary request/response pair.

    Deterministic and offline: hashing the same `(description, response)`
    pair twice produces byte-identical envelopes (a replay guarantee),
    changing either input changes the corresponding hash.
    """
    request = {
        "boundary": description.boundary,
        "name": description.name,
        "version": description.version,
        "operation": description.operation,
        "parameters": description.parameters,
    }
    return TaskEnvelope(
        protocol_hash=lock.protocol_hash,
        procedure_hash=lock.procedure_hash,
        schema_hash=lock.schema_hash,
        input_hash=lock.input_hash,
        adapter=description.name,
        adapter_version=description.version,
        provider=provider_or_source,
        model=model_or_target,
        decoding={},
        request_hash=object_hash(request),
        response_hash=object_hash(dict(response)),
    )


class PubMedArticleCanary(ConnectorBoundary):
    """Bounded canary: fetch one known PubMed article by PMID.

    Requests only `pmid` plus a fixed, minimal field list. Never infers
    full-text availability; the reported access state is validated, not
    assumed.
    """

    name = "pubmed-article-canary"
    version = "1"
    source = "pubmed"
    fixture_pmid = PUBMED_CANARY_PMID

    SCHEMA = CanaryRequestSchema(
        version=CANARY_SCHEMA_VERSION,
        canary=name,
        boundary="connector",
        operation="fetch_article_by_pmid",
        required_identifier_fields=("pmid",),
        allowed_access_states=(
            AccessState.METADATA_ONLY,
            AccessState.ABSTRACT_AVAILABLE,
            AccessState.FULL_TEXT_AVAILABLE,
            AccessState.PAYWALLED,
            AccessState.UNRESOLVED,
        ),
    )

    def describe_query(self, params: Mapping[str, Any]) -> LiveRequestDescription:
        pmid = str(params.get("pmid", "")).strip()
        if not pmid:
            raise ValueError("pubmed canary requires a non-empty 'pmid'")
        if pmid != self.fixture_pmid:
            raise IntegrityError(
                f"pubmed canary is pinned to PMID {self.fixture_pmid}"
            )
        return LiveRequestDescription(
            boundary="connector",
            name=self.name,
            version=self.version,
            operation=self.SCHEMA.operation,
            parameters={"pmid": pmid, "fields": ["pmid", "title", "doi", "access_state"]},
        )

    def verify_response(self, params: Mapping[str, Any], response: Mapping[str, Any]) -> AccessState:
        self.describe_query(params)
        verify_identity({"pmid": str(params.get("pmid", "")).strip()}, response)
        state = validate_access_state(str(response.get("access_state", "")))
        if state not in self.SCHEMA.allowed_access_states:
            raise IntegrityError(f"pubmed canary access state not permitted: {state.value}")
        return state

    def _execute_request_live(self, request: LiveRequestDescription) -> Mapping[str, Any]:
        if request != self.describe_query({"pmid": self.fixture_pmid}):
            raise IntegrityError("pubmed canary live request is not the pinned fixture")
        raise NotImplementedError("PubMed live canary is not implemented in the offline build")


class ClinicalTrialsRecordCanary(ConnectorBoundary):
    """Bounded canary: fetch one known ClinicalTrials.gov record by NCT id."""

    name = "clinicaltrials-record-canary"
    version = "1"
    source = "clinicaltrials.gov"
    fixture_nct_id = CLINICALTRIALS_CANARY_NCT_ID

    SCHEMA = CanaryRequestSchema(
        version=CANARY_SCHEMA_VERSION,
        canary=name,
        boundary="connector",
        operation="fetch_record_by_nct_id",
        required_identifier_fields=("nct_id",),
        allowed_access_states=(
            AccessState.METADATA_ONLY,
            AccessState.FULL_TEXT_AVAILABLE,
            AccessState.UNRESOLVED,
        ),
    )

    def describe_query(self, params: Mapping[str, Any]) -> LiveRequestDescription:
        nct_id = str(params.get("nct_id", "")).strip()
        if not nct_id:
            raise ValueError("clinicaltrials canary requires a non-empty 'nct_id'")
        if nct_id != self.fixture_nct_id:
            raise IntegrityError(
                f"clinicaltrials canary is pinned to {self.fixture_nct_id}"
            )
        return LiveRequestDescription(
            boundary="connector",
            name=self.name,
            version=self.version,
            operation=self.SCHEMA.operation,
            parameters={"nct_id": nct_id, "fields": ["nct_id", "title", "access_state"]},
        )

    def verify_response(self, params: Mapping[str, Any], response: Mapping[str, Any]) -> AccessState:
        self.describe_query(params)
        verify_identity({"nct_id": str(params.get("nct_id", "")).strip()}, response)
        state = validate_access_state(str(response.get("access_state", "")))
        if state not in self.SCHEMA.allowed_access_states:
            raise IntegrityError(f"clinicaltrials canary access state not permitted: {state.value}")
        return state

    def _execute_request_live(self, request: LiveRequestDescription) -> Mapping[str, Any]:
        if request != self.describe_query({"nct_id": self.fixture_nct_id}):
            raise IntegrityError("ClinicalTrials.gov live request is not the pinned fixture")
        raise NotImplementedError("ClinicalTrials.gov live canary is not implemented in the offline build")


class SingleLlmScreeningCanary(ProviderBoundary):
    """Bounded canary: one fixed screening call to a single LLM provider.

    Uses the same `include`/`exclude`/`abstain` decision vocabulary as the
    offline screening adapter (`screen_prove.models.ScreenDecision`); a
    canary response outside that vocabulary is rejected, not coerced.
    """

    name = "llm-screening-canary"
    version = "1"
    provider = "canary"
    model = "canary-fixture"
    fixture_record_id = LLM_CANARY_RECORD_ID
    fixture_title = LLM_CANARY_TITLE

    SCHEMA = CanaryRequestSchema(
        version=CANARY_SCHEMA_VERSION,
        canary=name,
        boundary="provider",
        operation="screen_one_record",
        required_identifier_fields=("record_id",),
        allowed_access_states=(),
    )

    def describe_request(self, record: Mapping[str, Any]) -> LiveRequestDescription:
        record_id = str(record.get("record_id", "")).strip()
        if not record_id:
            raise ValueError("llm screening canary requires a non-empty 'record_id'")
        if record_id != self.fixture_record_id:
            raise IntegrityError(
                f"llm screening canary is pinned to record {self.fixture_record_id}"
            )
        title = str(record.get("title", "")).strip()
        if title != self.fixture_title:
            raise IntegrityError(
                f"llm screening canary is pinned to title {self.fixture_title!r}"
            )
        return LiveRequestDescription(
            boundary="provider",
            name=self.name,
            version=self.version,
            operation=self.SCHEMA.operation,
            parameters={"record_id": record_id, "title": self.fixture_title},
        )

    def verify_response(self, record: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        self.describe_request(record)
        verify_identity({"record_id": str(record.get("record_id", "")).strip()}, response)
        try:
            ScreenDecision(response.get("decision"))
        except ValueError as exc:
            raise IntegrityError(
                f"llm screening canary reported an invalid decision: {response.get('decision')!r}"
            ) from exc

    def _execute_request_live(self, request: LiveRequestDescription) -> Mapping[str, Any]:
        expected = self.describe_request({
            "record_id": self.fixture_record_id,
            "title": self.fixture_title,
        })
        if request != expected:
            raise IntegrityError("LLM live request is not the pinned fixture")
        raise NotImplementedError("LLM live canary is not implemented in the offline build")
