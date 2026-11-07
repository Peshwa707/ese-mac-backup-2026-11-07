import json
import inspect

import pytest

from full_text_config import (
    ACCESS_CONFIG_VERSION,
    AccessConfigError,
    access_config_registry,
    validate_access_config,
)
import full_text_access
import full_text_config


def test_registry_exposes_named_institutional_modes_without_values():
    registry = access_config_registry()
    ids = [entry["connector_id"] for entry in registry]
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
    scopus = next(entry for entry in registry if entry["connector_id"] == "scopus")
    assert "api_key_reference" in scopus["modes"]
    assert "manual_browser" in scopus["modes"]


def test_validate_reference_only_scopus_embase_and_ezproxy_profile():
    profile = validate_access_config(
        {
            "connectors": [
                {
                    "connector_id": "scopus",
                    "enabled": True,
                    "mode": "api_key_reference",
                    "credential_ref": {"kind": "env", "name": "SCOPUS_API_KEY"},
                    "institution_label": "Brown Health",
                },
                {
                    "connector_id": "embase",
                    "enabled": True,
                    "mode": "manual_browser",
                    "institution_label": "Brown Health",
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
    assert profile["config_version"] == ACCESS_CONFIG_VERSION
    assert [entry["connector_id"] for entry in profile["connectors"]] == [
        "scopus",
        "embase",
        "ezproxy",
    ]
    assert profile["connectors"][0]["credential_ref"] == {
        "kind": "env",
        "name": "SCOPUS_API_KEY",
    }
    assert "SCOPUS_API_KEY" in json.dumps(profile)
    assert "SCOPUS_SECRET_VALUE" not in json.dumps(profile)


def test_profile_is_deterministic_and_rejects_secret_values():
    config = {
        "connectors": [
            {
                "connector_id": "embase",
                "enabled": True,
                "mode": "api_key_reference",
                "credential_ref": {"kind": "env", "name": "EMBASE_API_KEY"},
            }
        ]
    }
    assert validate_access_config(config) == validate_access_config(config)
    bad = {
        "connectors": [
            {
                "connector_id": "scopus",
                "enabled": True,
                "mode": "api_key_reference",
                "credential_ref": {"kind": "env", "name": "sk-live-SECRET"},
            }
        ]
    }
    with pytest.raises(AccessConfigError) as exc:
        validate_access_config(bad)
    assert "sk-live-SECRET" not in str(exc.value)


@pytest.mark.parametrize(
    "entry",
    [
        {"connector_id": "google_scholar", "enabled": True, "mode": "api_key_reference"},
        {
            "connector_id": "ezproxy",
            "enabled": True,
            "mode": "proxy_url",
            "proxy_base_url": "http://library.example.edu/login",
        },
        {
            "connector_id": "scopus",
            "enabled": True,
            "mode": "api_key_reference",
        },
        {
            "connector_id": "scopus",
            "enabled": True,
            "mode": "api_key_reference",
            "credential_ref": {"kind": "env", "name": "SCOPUS_API_KEY"},
            "credential_value": "should-not-be-accepted",
        },
    ],
)
def test_invalid_or_incomplete_profiles_fail_closed(entry):
    with pytest.raises(AccessConfigError):
        validate_access_config({"connectors": [entry]})


def test_profile_rejects_duplicates_and_unknown_connectors():
    with pytest.raises(AccessConfigError):
        validate_access_config(
            {
                "connectors": [
                    {"connector_id": "scopus", "enabled": False, "mode": "manual_browser"},
                    {"connector_id": "scopus", "enabled": True, "mode": "manual_browser"},
                ]
            }
        )
    with pytest.raises(AccessConfigError):
        validate_access_config(
            {"connectors": [{"connector_id": "sci-hub", "enabled": True, "mode": "manual_browser"}]}
        )


def test_proxy_url_rejects_embedded_credentials_and_query_data():
    for proxy_url in (
        "https://user:password@library.example.edu/login",
        "https://library.example.edu/login?next=publisher",
    ):
        with pytest.raises(AccessConfigError):
            validate_access_config(
                {
                    "connectors": [
                        {
                            "connector_id": "ezproxy",
                            "enabled": True,
                            "mode": "proxy_url",
                            "proxy_base_url": proxy_url,
                        }
                    ]
                }
            )


def test_full_text_modules_have_no_live_io_or_secret_read_path():
    source = (inspect.getsource(full_text_access) + inspect.getsource(full_text_config)).lower()
    for forbidden in (
        "urllib.request",
        "requests.",
        "urlopen",
        "os.environ",
        "keyring",
        "subprocess",
        "webbrowser",
    ):
        assert forbidden not in source
