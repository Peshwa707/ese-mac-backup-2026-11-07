import json
import inspect

import pytest

import manual_access_record
from full_text_access import ConnectorInputError
from manual_access_record import (
    MANUAL_RECORD_VERSION,
    ManualRecordError,
    record_manual_access,
)


def citation():
    return {
        "doi": "10.1000/ABC.1",
        "pmid": "39551604",
        "title": "A bounded fixture article",
    }


def test_manual_full_text_record_requires_matching_identity_and_preserves_provenance():
    record = record_manual_access(
        citation(),
        route_id="ezproxy",
        access_state="full_text_observed",
        candidate={
            "doi": "10.1000/abc.1",
            "pmid": "39551604",
            "title": "A bounded fixture article",
        },
        source_url="https://library.example.edu/article/fixture",
        operator_note="Observed after user-owned institutional login.",
    )
    assert record["record_version"] == MANUAL_RECORD_VERSION
    assert record["original_citation"] == citation()
    assert record["identity_check"] == {"match": True, "mismatches": []}
    assert record["verified_full_text"] is True
    assert "login" in record["operator_note"]
    assert len(record["record_id"]) == 64


@pytest.mark.parametrize("route_id", ["google_scholar", "publisher", "interlibrary_loan", "manual_browser"])
def test_manual_routes_are_supported_but_never_automated(route_id):
    record = record_manual_access(
        {"title": "Title only"},
        route_id=route_id,
        access_state="abstract_only",
        candidate={"title": "Title only"},
    )
    assert record["verified_full_text"] is False
    assert record["route_id"] == route_id


def test_abstract_or_login_states_do_not_claim_full_text():
    for state in ("abstract_only", "login_required", "not_found", "ill_requested"):
        record = record_manual_access(
            citation(),
            route_id="scopus",
            access_state=state,
            candidate={"doi": "10.1000/abc.1"},
        )
        assert record["verified_full_text"] is False


def test_mismatched_identity_and_unsafe_url_fail_closed_without_echo():
    with pytest.raises(ManualRecordError):
        record_manual_access(
            citation(),
            route_id="publisher",
            access_state="full_text_observed",
            candidate={"doi": "10.1000/other", "pmid": "39551604"},
            source_url="https://publisher.example/article",
        )
    with pytest.raises(ManualRecordError) as exc:
        record_manual_access(
            citation(),
            route_id="publisher",
            access_state="full_text_observed",
            candidate={"doi": "sk-live-secret"},
            source_url="https://user:pass@publisher.example/article?token=secret",
        )
    assert "sk-live-secret" not in str(exc.value)


def test_full_text_observed_rejects_blank_identity_fields():
    with pytest.raises(ManualRecordError):
        record_manual_access(
            citation(),
            route_id="publisher",
            access_state="full_text_observed",
            candidate={
                "doi": "10.1000/abc.1",
                "pmid": "39551604",
                "title": "   ",
            },
            source_url="https://publisher.example/article",
        )


def test_manual_record_rejects_secret_shaped_input_and_is_deterministic():
    with pytest.raises(ConnectorInputError):
        record_manual_access(
            {"title": "sk-ant-secret"},
            route_id="google_scholar",
            access_state="abstract_only",
            candidate={"title": "fixture"},
        )
    left = record_manual_access(
        citation(), route_id="ezproxy", access_state="login_required", candidate={}
    )
    right = record_manual_access(
        citation(), route_id="ezproxy", access_state="login_required", candidate={}
    )
    assert json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def test_manual_record_module_has_no_external_access_path():
    source = inspect.getsource(manual_access_record).lower()
    for forbidden in (
        "urlopen",
        "requests.",
        "os.environ",
        "keyring",
        "subprocess",
        "webbrowser",
    ):
        assert forbidden not in source
