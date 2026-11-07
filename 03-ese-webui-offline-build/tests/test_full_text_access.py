import json

import pytest

from full_text_access import (
    ACCESS_PLAN_VERSION,
    ConnectorInputError,
    ConnectorInputError,
    build_access_plan,
    compare_identity,
    connector_registry,
)
from full_text_config import validate_access_config


def citation():
    return {
        "doi": "https://doi.org/10.1000/ABC.1",
        "pmid": "39551604",
        "title": "A bounded fixture article",
        "journal": "Fixture Journal",
        "year": 2024,
    }


def test_registry_lists_requested_institutional_and_lawful_routes():
    ids = [entry["connector_id"] for entry in connector_registry()]
    assert ids == [
        "pubmed_central",
        "crossref",
        "google_scholar",
        "scopus",
        "embase",
        "ezproxy",
        "publisher",
        "interlibrary_loan",
        "manual_browser",
    ]


def test_plan_preserves_original_citation_and_normalizes_identity():
    original = citation()
    plan = build_access_plan(original)
    assert plan["access_version"] == ACCESS_PLAN_VERSION
    assert plan["original_citation"] == original
    assert plan["identity"] == {
        "doi": "10.1000/abc.1",
        "pmid": "39551604",
        "title": "A bounded fixture article",
        "journal": "Fixture Journal",
        "year": 2024,
    }
    assert len(plan["plan_id"]) == 64


def test_plan_is_deterministic_and_contains_no_credentials_or_network_result():
    left = build_access_plan(citation())
    right = build_access_plan(citation())
    assert json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )
    assert "response" not in left
    assert "credential" not in json.dumps(left).lower()
    assert {route["connector_id"] for route in left["routes"]} == {
        "pubmed_central",
        "crossref",
        "google_scholar",
        "scopus",
        "embase",
        "ezproxy",
        "publisher",
        "interlibrary_loan",
        "manual_browser",
    }


def test_route_states_do_not_claim_subscription_or_discovery_is_full_text():
    plan = build_access_plan(citation())
    routes = {route["connector_id"]: route for route in plan["routes"]}
    assert routes["pubmed_central"]["access_state"] == "open_access_candidate"
    assert routes["google_scholar"]["access_state"] == "discovery_manual"
    assert routes["scopus"]["access_state"] == "metadata_or_entitlement_link"
    assert routes["embase"]["access_state"] == "metadata_or_entitlement_link"
    assert routes["ezproxy"]["access_state"] == "institutional_login_required"
    assert routes["publisher"]["access_state"] == "publisher_access_check"
    assert routes["interlibrary_loan"]["access_state"] == "library_request"
    assert routes["manual_browser"]["access_state"] == "manual_user_session"
    assert all(route["verified_full_text"] is False for route in routes.values())


def test_enabled_connector_order_is_canonical_and_unknown_is_rejected():
    plan = build_access_plan(
        citation(), enabled_connectors=["ezproxy", "scopus", "pubmed_central"]
    )
    assert [route["connector_id"] for route in plan["routes"]] == [
        "pubmed_central",
        "scopus",
        "ezproxy",
    ]
    with pytest.raises(ConnectorInputError):
        build_access_plan(citation(), enabled_connectors=["sci-hub"])


def test_secret_shaped_citation_is_rejected_without_echo():
    bad = citation()
    bad["title"] = "sk-ant-fixture-secret"
    with pytest.raises(ConnectorInputError) as exc:
        build_access_plan(bad)
    assert "sk-ant" not in str(exc.value)


def test_identity_comparison_reports_exact_mismatch_without_fetching():
    assert compare_identity(
        citation(),
        {"doi": "10.1000/abc.1", "pmid": "39551604", "title": "A bounded fixture article"},
    ) == {"match": True, "mismatches": []}
    result = compare_identity(citation(), {"doi": "10.1000/other", "pmid": "39551604"})
    assert result == {"match": False, "mismatches": ["doi"]}


def test_missing_identity_is_allowed_but_warned():
    plan = build_access_plan({"title": "Title only"})
    assert plan["identity"] == {
        "doi": None,
        "pmid": None,
        "title": "Title only",
        "journal": None,
        "year": None,
    }
    assert "citation has no DOI or PMID" in plan["warnings"]


def test_access_profile_marks_configured_institutional_routes_without_resolving_them():
    profile = validate_access_config(
        {
            "connectors": [
                {
                    "connector_id": "scopus",
                    "enabled": True,
                    "mode": "api_key_reference",
                    "credential_ref": {"kind": "env", "name": "SCOPUS_API_KEY"},
                },
                {
                    "connector_id": "ezproxy",
                    "enabled": True,
                    "mode": "proxy_url",
                    "proxy_base_url": "https://library.example.edu/login",
                },
            ]
        }
    )
    plan = build_access_plan(citation(), access_config=profile)
    routes = {route["connector_id"]: route for route in plan["routes"]}
    assert routes["scopus"]["configuration_state"] == "configured_reference_only"
    assert routes["ezproxy"]["configuration_state"] == "configured_manual_route"
    assert routes["scopus"]["verified_full_text"] is False
    assert routes["ezproxy"]["verified_full_text"] is False


def test_access_profile_tampering_is_rejected_before_route_planning():
    profile = validate_access_config(
        {
            "connectors": [
                {
                    "connector_id": "scopus",
                    "enabled": True,
                    "mode": "api_key_reference",
                    "credential_ref": {"kind": "env", "name": "SCOPUS_API_KEY"},
                }
            ]
        }
    )
    profile["connectors"][0]["enabled"] = False
    with pytest.raises(ConnectorInputError):
        build_access_plan(citation(), access_config=profile)
    profile = validate_access_config(
        {
            "connectors": [
                {
                    "connector_id": "ezproxy",
                    "enabled": True,
                    "mode": "proxy_url",
                    "proxy_base_url": "https://library.example.edu/login",
                }
            ]
        }
    )
    profile["config_version"] = 999
    with pytest.raises(ConnectorInputError):
        build_access_plan(citation(), access_config=profile)


def test_identity_comparison_treats_blank_candidate_title_as_absent():
    assert compare_identity(citation(), {"title": "   "}) == {
        "match": True,
        "mismatches": [],
    }
