import pytest

from screen_prove.canaries import (
    CANARY_SCHEMA_VERSION,
    CLINICALTRIALS_CANARY_NCT_ID,
    AccessState,
    ClinicalTrialsRecordCanary,
    LLM_CANARY_RECORD_ID,
    LLM_CANARY_TITLE,
    PubMedArticleCanary,
    PUBMED_CANARY_PMID,
    SingleLlmScreeningCanary,
    build_task_envelope,
    validate_access_state,
    verify_identity,
)
from screen_prove.contracts import IntegrityError, ProtocolLock, TaskEnvelope
from screen_prove.live_boundary import LIVE_SMOKE_OPT_IN_ENV_VAR, LiveExecutionNotAuthorized, LiveRequestDescription


def _lock() -> ProtocolLock:
    return ProtocolLock({"eligibility": ["RCT"]}, {"steps": ["screen"]}, {"decision": "enum"}, "a" * 64)


# --- capability/access states -----------------------------------------------


def test_validate_access_state_accepts_every_documented_state():
    for value in (
        "metadata_only",
        "abstract_available",
        "full_text_available",
        "paywalled",
        "login_required",
        "institutional_access_required",
        "unresolved",
    ):
        assert validate_access_state(value) is AccessState(value)


def test_validate_access_state_rejects_an_unbounded_value():
    with pytest.raises(IntegrityError, match="access state"):
        validate_access_state("full_text_guessed")


# --- identity checks ----------------------------------------------------------


def test_verify_identity_passes_on_matching_identifiers():
    verify_identity({"pmid": "123456"}, {"pmid": "123456", "title": "A trial"})


def test_verify_identity_rejects_a_mismatched_identifier():
    with pytest.raises(IntegrityError, match="identity mismatch"):
        verify_identity({"pmid": "123456"}, {"pmid": "999999"})


def test_verify_identity_rejects_a_missing_identifier():
    with pytest.raises(IntegrityError, match="missing identifier"):
        verify_identity({"pmid": "123456"}, {"title": "A trial"})


def test_verify_identity_normalizes_doi_case():
    verify_identity({"doi": "10.1000/ABCD"}, {"doi": "10.1000/abcd"})


# --- PubMed canary -------------------------------------------------------------


def test_pubmed_canary_describe_query_is_deterministic_and_bounded():
    canary = PubMedArticleCanary()
    description = canary.describe_query({"pmid": PUBMED_CANARY_PMID})
    assert description.operation == PubMedArticleCanary.SCHEMA.operation
    assert description.parameters["pmid"] == PUBMED_CANARY_PMID
    assert canary.describe_query({"pmid": PUBMED_CANARY_PMID}) == description


def test_pubmed_canary_describe_query_requires_a_pmid():
    canary = PubMedArticleCanary()
    with pytest.raises(ValueError, match="pmid"):
        canary.describe_query({})


def test_pubmed_canary_rejects_non_fixture_pmid():
    with pytest.raises(IntegrityError, match="pinned"):
        PubMedArticleCanary().describe_query({"pmid": "123456"})


def test_pubmed_canary_verify_response_checks_identity_and_access_state():
    canary = PubMedArticleCanary()
    state = canary.verify_response({"pmid": PUBMED_CANARY_PMID}, {"pmid": PUBMED_CANARY_PMID, "access_state": "abstract_available"})
    assert state is AccessState.ABSTRACT_AVAILABLE


def test_pubmed_canary_verify_response_rejects_identity_mismatch():
    canary = PubMedArticleCanary()
    with pytest.raises(IntegrityError, match="identity mismatch"):
        canary.verify_response({"pmid": PUBMED_CANARY_PMID}, {"pmid": "654321", "access_state": "metadata_only"})


def test_pubmed_canary_verify_response_rejects_a_disallowed_access_state():
    canary = PubMedArticleCanary()
    with pytest.raises(IntegrityError, match="not permitted"):
        canary.verify_response({"pmid": PUBMED_CANARY_PMID}, {"pmid": PUBMED_CANARY_PMID, "access_state": "login_required"})


def test_pubmed_canary_never_executes_without_opt_in(monkeypatch):
    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)
    canary = PubMedArticleCanary()
    description = canary.describe_query({"pmid": PUBMED_CANARY_PMID})
    with pytest.raises(LiveExecutionNotAuthorized):
        canary.fetch_live(description)


def test_pubmed_canary_rejects_direct_non_fixture_description_after_opt_in(monkeypatch):
    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    canary = PubMedArticleCanary()
    malicious = canary.describe_query({"pmid": PUBMED_CANARY_PMID})
    malicious = malicious.__class__(
        boundary=malicious.boundary,
        name=malicious.name,
        version=malicious.version,
        operation=malicious.operation,
        parameters={"pmid": "999999", "fields": ["pmid", "title", "doi", "access_state"]},
    )
    with pytest.raises(IntegrityError, match="pinned fixture"):
        canary.fetch_live(malicious)


# --- ClinicalTrials.gov canary --------------------------------------------------


def test_clinical_trials_canary_describe_query_requires_nct_id():
    canary = ClinicalTrialsRecordCanary()
    with pytest.raises(ValueError, match="nct_id"):
        canary.describe_query({})


def test_clinical_trials_canary_describe_query_is_deterministic_and_bounded():
    canary = ClinicalTrialsRecordCanary()
    description = canary.describe_query({"nct_id": CLINICALTRIALS_CANARY_NCT_ID})
    assert description.operation == ClinicalTrialsRecordCanary.SCHEMA.operation
    assert description.parameters["nct_id"] == CLINICALTRIALS_CANARY_NCT_ID
    assert canary.describe_query({"nct_id": CLINICALTRIALS_CANARY_NCT_ID}) == description


def test_clinical_trials_canary_verify_response_checks_identity():
    canary = ClinicalTrialsRecordCanary()
    state = canary.verify_response(
        {"nct_id": CLINICALTRIALS_CANARY_NCT_ID}, {"nct_id": CLINICALTRIALS_CANARY_NCT_ID, "access_state": "metadata_only"}
    )
    assert state is AccessState.METADATA_ONLY


def test_clinical_trials_canary_verify_response_rejects_a_disallowed_access_state():
    canary = ClinicalTrialsRecordCanary()
    with pytest.raises(IntegrityError, match="not permitted"):
        canary.verify_response({"nct_id": CLINICALTRIALS_CANARY_NCT_ID}, {"nct_id": CLINICALTRIALS_CANARY_NCT_ID, "access_state": "paywalled"})


def test_clinical_trials_canary_never_executes_without_opt_in(monkeypatch):
    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)
    canary = ClinicalTrialsRecordCanary()
    description = canary.describe_query({"nct_id": CLINICALTRIALS_CANARY_NCT_ID})
    with pytest.raises(LiveExecutionNotAuthorized):
        canary.fetch_live(description)


def test_clinical_trials_canary_rejects_direct_non_fixture_description_after_opt_in(monkeypatch):
    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    canary = ClinicalTrialsRecordCanary()
    description = canary.describe_query({"nct_id": CLINICALTRIALS_CANARY_NCT_ID})
    malicious = description.__class__(
        boundary=description.boundary,
        name=description.name,
        version=description.version,
        operation=description.operation,
        parameters={"nct_id": "NCT99999999", "fields": ["nct_id", "title", "access_state"]},
    )
    with pytest.raises(IntegrityError, match="pinned fixture"):
        canary.fetch_live(malicious)


# --- one LLM canary --------------------------------------------------------------


def test_llm_canary_describe_request_requires_record_id():
    canary = SingleLlmScreeningCanary()
    with pytest.raises(ValueError, match="record_id"):
        canary.describe_request({})


def test_llm_canary_describe_request_is_deterministic_and_bounded():
    canary = SingleLlmScreeningCanary()
    description = canary.describe_request({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE})
    assert description.operation == SingleLlmScreeningCanary.SCHEMA.operation
    assert description.parameters["record_id"] == LLM_CANARY_RECORD_ID
    assert canary.describe_request({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE}) == description


def test_llm_canary_verify_response_validates_decision_vocabulary():
    canary = SingleLlmScreeningCanary()
    canary.verify_response({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE}, {"record_id": LLM_CANARY_RECORD_ID, "decision": "abstain"})
    with pytest.raises(IntegrityError, match="invalid decision"):
        canary.verify_response({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE}, {"record_id": LLM_CANARY_RECORD_ID, "decision": "maybe"})


def test_llm_canary_verify_response_rejects_identity_mismatch():
    canary = SingleLlmScreeningCanary()
    with pytest.raises(IntegrityError, match="identity mismatch"):
        canary.verify_response({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE}, {"record_id": "r2", "decision": "include"})


def test_llm_canary_never_executes_without_opt_in(monkeypatch):
    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)
    canary = SingleLlmScreeningCanary()
    description = canary.describe_request({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE})
    with pytest.raises(LiveExecutionNotAuthorized):
        canary.execute_live(description)


def test_llm_canary_rejects_direct_non_fixture_description_after_opt_in(monkeypatch):
    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    canary = SingleLlmScreeningCanary()
    description = canary.describe_request({"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE})
    malicious = description.__class__(
        boundary=description.boundary,
        name=description.name,
        version=description.version,
        operation=description.operation,
        parameters={"record_id": "fixture-record-other", "title": "unapproved title"},
    )
    with pytest.raises(IntegrityError, match="pinned fixture"):
        canary.execute_live(malicious)


# --- task-envelope fields / replay -----------------------------------------------


def test_build_task_envelope_is_byte_stable_for_the_same_inputs():
    lock = _lock()
    canary = PubMedArticleCanary()
    description = canary.describe_query({"pmid": PUBMED_CANARY_PMID})
    response = {"pmid": PUBMED_CANARY_PMID, "title": "A trial", "access_state": "abstract_available"}

    envelope_a = build_task_envelope(lock, description, "pubmed", "canary-fixture", response)
    envelope_b = build_task_envelope(lock, description, "pubmed", "canary-fixture", response)

    assert envelope_a.to_dict() == envelope_b.to_dict()
    assert isinstance(envelope_a, TaskEnvelope)


def test_build_task_envelope_response_hash_changes_when_the_response_changes():
    lock = _lock()
    canary = PubMedArticleCanary()
    description = canary.describe_query({"pmid": PUBMED_CANARY_PMID})

    envelope_a = build_task_envelope(
        lock, description, "pubmed", "canary-fixture", {"pmid": PUBMED_CANARY_PMID, "access_state": "metadata_only"}
    )
    envelope_b = build_task_envelope(
        lock, description, "pubmed", "canary-fixture", {"pmid": PUBMED_CANARY_PMID, "access_state": "abstract_available"}
    )

    assert envelope_a.response_hash != envelope_b.response_hash


def test_build_task_envelope_request_hash_changes_when_the_description_changes():
    lock = _lock()
    canary = PubMedArticleCanary()
    response = {"pmid": PUBMED_CANARY_PMID, "access_state": "metadata_only"}

    envelope_a = build_task_envelope(
        lock, canary.describe_query({"pmid": PUBMED_CANARY_PMID}), "pubmed", "canary-fixture", response
    )
    altered_description = LiveRequestDescription(
        boundary="connector",
        name=canary.name,
        version=canary.version,
        operation="fetch_article_by_pmid_altered_fixture",
        parameters={"pmid": PUBMED_CANARY_PMID, "fields": ["pmid"]},
    )
    envelope_b = build_task_envelope(
        lock, altered_description, "pubmed", "canary-fixture", response
    )

    assert envelope_a.request_hash != envelope_b.request_hash


def test_schema_to_dict_is_versioned_and_json_stable():
    schema = PubMedArticleCanary.SCHEMA.to_dict()
    assert schema["version"] == CANARY_SCHEMA_VERSION
    assert schema["required_identifier_fields"] == ["pmid"]
    assert schema == PubMedArticleCanary.SCHEMA.to_dict()
