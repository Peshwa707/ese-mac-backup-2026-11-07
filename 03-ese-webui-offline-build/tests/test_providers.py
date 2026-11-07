"""Offline, deterministic tests for the credential-safe provider registry.

Everything in here validates pure configuration behavior: no network, no
credential resolution, no subscription/session automation, no engine calls.
The one ``monkeypatch.setenv`` use exists to prove the module does NOT read
environment values — not to simulate live access.
"""

import copy
from pathlib import Path

import pytest

from providers import (
    ACCESS_API_KEY,
    ACCESS_INTERACTIVE_SUBSCRIPTION,
    ACCESS_LOCAL_NO_AUTH,
    PROVIDER_IDS,
    ProviderConfigError,
    provider_registry,
    public_view,
    validate_provider_config,
)

PROVIDERS_SOURCE = Path(__file__).resolve().parent.parent / "providers.py"


# --- registry shape -------------------------------------------------------


def test_registry_enumerates_exactly_the_supported_providers():
    assert set(PROVIDER_IDS) == {
        "openai_api",
        "anthropic_api",
        "ollama_local",
        "ollama_cloud",
        "moonshot_kimi_api",
        "deepseek_api",
        "interactive_subscription",
    }
    registry = provider_registry()
    assert set(registry) == set(PROVIDER_IDS)


def test_api_providers_are_distinguished_from_consumer_subscriptions():
    registry = provider_registry()
    openai_notes = registry["openai_api"]["notes"].lower()
    anthropic_notes = registry["anthropic_api"]["notes"].lower()
    # The registry must name the distinction explicitly so an operator cannot
    # confuse a metered API key with a consumer subscription login.
    assert "subscription" in openai_notes and "chatgpt" in openai_notes
    assert "subscription" in anthropic_notes and (
        "pro" in anthropic_notes or "max" in anthropic_notes
    )
    assert registry["openai_api"]["access"] == ACCESS_API_KEY
    assert registry["anthropic_api"]["access"] == ACCESS_API_KEY


def test_ollama_local_and_cloud_are_distinct_modes():
    registry = provider_registry()
    assert registry["ollama_local"]["access"] == ACCESS_LOCAL_NO_AUTH
    assert registry["ollama_cloud"]["access"] == ACCESS_API_KEY
    assert registry["ollama_local"]["default_env_var"] is None
    assert registry["ollama_cloud"]["default_env_var"] == "OLLAMA_API_KEY"


def test_moonshot_and_deepseek_are_api_key_providers_with_env_defaults():
    registry = provider_registry()
    for provider_id, env_name in (
        ("moonshot_kimi_api", "MOONSHOT_API_KEY"),
        ("deepseek_api", "DEEPSEEK_API_KEY"),
    ):
        assert registry[provider_id]["access"] == ACCESS_API_KEY
        assert registry[provider_id]["default_env_var"] == env_name


def test_interactive_subscription_is_its_own_access_mode():
    registry = provider_registry()
    spec = registry["interactive_subscription"]
    assert spec["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION
    assert spec["default_env_var"] is None
    notes = spec["notes"].lower()
    # The mode must document that the human drives their own session and that
    # the WebUI stores no secret material for it.
    assert "cookie" in notes or "session" in notes


def test_registry_accessor_returns_copies_not_shared_state():
    first = provider_registry()
    first["openai_api"]["notes"] = "tampered"
    first["injected"] = {}
    second = provider_registry()
    assert "injected" not in second
    assert second["openai_api"]["notes"] != "tampered"


# --- credential references (never values) ---------------------------------


def test_env_credential_reference_is_accepted_by_name_only():
    config = validate_provider_config(
        {
            "provider": "openai_api",
            "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
        }
    )
    assert config["credential_ref"] == {"kind": "env", "name": "OPENAI_API_KEY"}
    assert config["live_enabled"] is False


@pytest.mark.parametrize(
    "bad_name",
    [
        "openai_api_key",  # lowercase: not an env-var-name shape
        "OPENAI API KEY",  # whitespace
        "OPENAI-API-KEY",  # hyphen outside the env grammar
        "1KEY",  # leading digit
        "A" * 65,  # over-length
        "",
    ],
)
def test_env_names_outside_the_strict_grammar_are_rejected(bad_name):
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {
                "provider": "openai_api",
                "credential_ref": {"kind": "env", "name": bad_name},
            }
        )


def test_keychain_reference_is_accepted_with_bounded_identifiers():
    config = validate_provider_config(
        {
            "provider": "anthropic_api",
            "credential_ref": {
                "kind": "keychain",
                "service": "com.neurologic.ese",
                "account": "anthropic-api-key",
            },
        }
    )
    assert config["credential_ref"]["service"] == "com.neurologic.ese"


@pytest.mark.parametrize(
    "ref",
    [
        {"kind": "env", "name": "OPENAI_API_KEY", "value": "anything"},
        {"kind": "env", "name": "OPENAI_API_KEY", "secret": "anything"},
        {"kind": "keychain", "service": "svc", "account": "acct", "token": "t"},
        {"kind": "env", "name": "OPENAI_API_KEY", "password": "p"},
        {"kind": "env", "name": "OPENAI_API_KEY", "api_key": "k"},
        {"kind": "env", "name": "OPENAI_API_KEY", "cookie": "c"},
        {"kind": "env", "name": "OPENAI_API_KEY", "session": "s"},
        {"kind": "env", "name": "OPENAI_API_KEY", "unexpected": "x"},
    ],
)
def test_credential_reference_dicts_with_secret_or_unknown_keys_are_rejected(ref):
    with pytest.raises(ProviderConfigError):
        validate_provider_config({"provider": "openai_api", "credential_ref": ref})


@pytest.mark.parametrize(
    "leaky",
    [
        "sk-abc123def456",
        "sk-ant-api03-xyz",
        "xoxb-1234-5678",
        "ghp_16charsformat",
        "gsk_live_material",
        "AKIAIOSFODNN7EXAMPLE",
        "Bearer eyJhbGciOi",
    ],
)
def test_secret_shaped_literals_are_rejected_wherever_they_appear(leaky):
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {
                "provider": "anthropic_api",
                "credential_ref": {"kind": "keychain", "service": leaky, "account": "a"},
            }
        )
    with pytest.raises(ProviderConfigError):
        validate_provider_config({"provider": "anthropic_api", "label": leaky})


@pytest.mark.parametrize("kind", ["file", "literal", "vault", ""])
def test_unknown_credential_reference_kinds_are_rejected(kind):
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {
                "provider": "deepseek_api",
                "credential_ref": {"kind": kind, "name": "DEEPSEEK_API_KEY"},
            }
        )


def test_ollama_local_rejects_any_credential_reference():
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {
                "provider": "ollama_local",
                "credential_ref": {"kind": "env", "name": "OLLAMA_API_KEY"},
            }
        )
    config = validate_provider_config({"provider": "ollama_local"})
    assert config["credential_ref"] is None


# --- interactive subscription boundary ------------------------------------


def test_interactive_subscription_accepts_only_known_services():
    for service in ("chatgpt", "claude"):
        config = validate_provider_config(
            {"provider": "interactive_subscription", "service": service}
        )
        assert config["service"] == service
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {"provider": "interactive_subscription", "service": "gemini"}
        )
    with pytest.raises(ProviderConfigError):
        validate_provider_config({"provider": "interactive_subscription"})


def test_interactive_subscription_rejects_credentials_and_session_material():
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {
                "provider": "interactive_subscription",
                "service": "chatgpt",
                "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
            }
        )
    for forbidden in ("cookie", "cookies", "session", "session_token", "browser_profile", "profile_path"):
        with pytest.raises(ProviderConfigError):
            validate_provider_config(
                {
                    "provider": "interactive_subscription",
                    "service": "claude",
                    forbidden: "anything",
                }
            )


def test_service_key_is_rejected_for_non_interactive_providers():
    with pytest.raises(ProviderConfigError):
        validate_provider_config({"provider": "openai_api", "service": "chatgpt"})


# --- safe defaults and the live gate --------------------------------------


def test_live_access_defaults_to_disabled():
    for provider_id in PROVIDER_IDS:
        payload = {"provider": provider_id}
        if provider_id == "interactive_subscription":
            payload["service"] = "chatgpt"
        config = validate_provider_config(payload)
        assert config["live_enabled"] is False


def test_enabling_live_access_is_rejected_pending_the_operator_gate():
    with pytest.raises(ProviderConfigError) as excinfo:
        validate_provider_config({"provider": "openai_api", "live_enabled": True})
    assert "operator" in str(excinfo.value).lower()


def test_operator_live_gate_key_is_rejected_as_not_yet_supported():
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {"provider": "openai_api", "operator_live_gate": {"confirmed": True}}
        )


@pytest.mark.parametrize("value", [1, "true", "false", None, 0])
def test_non_boolean_live_enabled_values_are_rejected(value):
    with pytest.raises(ProviderConfigError):
        validate_provider_config({"provider": "openai_api", "live_enabled": value})


# --- malformed configuration ----------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        "openai_api",
        {},
        {"provider": "unknown_provider"},
        {"provider": ""},
        {"provider": 7},
        {"provider": []},  # unhashable: must raise ProviderConfigError, not TypeError
        {"provider": {}},
        {"provider": None},
        {"provider": "openai_api", "surprise_key": True},
        {"provider": "openai_api", "credential_ref": "OPENAI_API_KEY"},
        {"provider": "openai_api", "credential_ref": []},
    ],
)
def test_malformed_or_unknown_configuration_is_rejected(config):
    with pytest.raises(ProviderConfigError):
        validate_provider_config(config)


def test_validation_is_pure_and_does_not_mutate_input():
    payload = {
        "provider": "moonshot_kimi_api",
        "credential_ref": {"kind": "env", "name": "MOONSHOT_API_KEY"},
    }
    snapshot = copy.deepcopy(payload)
    first = validate_provider_config(payload)
    second = validate_provider_config(payload)
    assert payload == snapshot
    assert first == second
    first["credential_ref"]["name"] = "TAMPERED"
    assert payload == snapshot


# --- no credential loading, no network, ever ------------------------------


def test_module_has_no_credential_resolver(monkeypatch):
    import providers as providers_module

    monkeypatch.setenv("OPENAI_API_KEY", "sk-proof-value-must-never-appear")
    exported = [name for name in dir(providers_module) if not name.startswith("_")]
    for name in exported:
        lowered = name.lower()
        assert "resolve" not in lowered
        assert "load_credential" not in lowered
        assert "read_secret" not in lowered


def test_public_view_never_contains_environment_values(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    config = validate_provider_config(
        {
            "provider": "openai_api",
            "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
        }
    )
    rendered = public_view(config)
    assert sentinel not in repr(rendered)
    assert sentinel not in repr(provider_registry())
    # The reference NAME (non-secret) is what the operator sees.
    assert "OPENAI_API_KEY" in repr(rendered)


def test_public_view_raises_rather_than_leaking_secret_shaped_strings():
    # Defense in depth: if validation were ever bypassed, serialization must
    # refuse to emit secret-shaped material instead of silently redacting it.
    tampered = validate_provider_config(
        {
            "provider": "openai_api",
            "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
        }
    )
    tampered["credential_ref"]["name"] = "sk-injected-after-validation"
    with pytest.raises(ProviderConfigError):
        public_view(tampered)


@pytest.mark.parametrize(
    "tampered_config",
    [
        # secret nested inside a list value
        {"provider": "openai_api", "extras": ["sk-nested-in-list"]},
        # secret two dictionary levels deep
        {"provider": "openai_api", "extras": {"inner": {"deeper": "sk-two-levels-down"}}},
        # secret inside a list inside a nested dict
        {"provider": "openai_api", "extras": {"inner": ["sk-list-in-dict"]}},
        # secret-shaped dictionary KEY, not value
        {"provider": "openai_api", "extras": {"sk-key-material": True}},
        # secret in a tuple
        {"provider": "openai_api", "extras": ("sk-in-tuple",)},
        # secret inside a hashable CONTAINER used as a dict key
        {"provider": "openai_api", "extras": {("sk-secret-in-tuple-key",): True}},
        {"provider": "openai_api", "extras": {frozenset({"sk-frozenset-key"}): 1}},
        # secret in a tuple key nested one dict deeper
        {"provider": "openai_api", "extras": {"inner": {(1, ("sk-deep-tuple-key",)): "x"}}},
    ],
)
def test_public_view_screens_nested_structures_recursively(tampered_config):
    # public_view's guarantee is defense in depth against post-validation
    # tampering, so it must screen arbitrarily nested keys and values —
    # not just top-level strings and one dictionary level.
    with pytest.raises(ProviderConfigError):
        public_view(tampered_config)


def test_screen_survives_cyclic_structures_instead_of_recursing_forever():
    # A tampered config can carry a reference cycle (deepcopy preserves
    # cycles); screening must terminate, not hit RecursionError.
    benign: dict = {"provider": "openai_api", "extras": {}}
    benign["extras"]["self"] = benign["extras"]
    rendered = public_view(benign)
    assert rendered["provider"] == "openai_api"

    leaky: dict = {"provider": "openai_api", "extras": {}}
    leaky["extras"]["self"] = leaky["extras"]
    leaky["extras"]["oops"] = "sk-secret-in-cycle"
    with pytest.raises(ProviderConfigError):
        public_view(leaky)


def test_validate_screens_nested_secret_material_in_config_values():
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            {
                "provider": "openai_api",
                "credential_ref": {"kind": "env", "name": {"deep": ["sk-nested-secret"]}},
            }
        )


def test_error_messages_never_echo_secret_shaped_input():
    # Exception text is a leakage surface (it gets logged). A rejected
    # config's offending VALUE must not ride out inside the error message.
    leaky = "sk-super-secret-material-12345"
    for config in (
        {"provider": leaky},
        {"provider": "openai_api", leaky: True},
        {
            "provider": "openai_api",
            "credential_ref": {"kind": leaky, "name": "OPENAI_API_KEY"},
        },
    ):
        with pytest.raises(ProviderConfigError) as excinfo:
            validate_provider_config(config)
        assert leaky not in str(excinfo.value)


def test_module_source_imports_no_network_or_process_machinery():
    source = PROVIDERS_SOURCE.read_text(encoding="utf-8")
    for forbidden in (
        "import socket",
        "import urllib",
        "from urllib",
        "import http",
        "from http",
        "import requests",
        "import subprocess",
        "from subprocess",
    ):
        assert forbidden not in source, forbidden
