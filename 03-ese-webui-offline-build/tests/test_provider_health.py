"""Offline, deterministic tests for the provider health/check boundary.

Everything here exercises pure configuration behavior. No network, no
credential resolution, no subscription/session automation. The
``monkeypatch.setenv`` uses exist to PROVE the module never reads
environment values — not to simulate live access.
"""

import copy
import json
from pathlib import Path

import pytest

from provider_health import (
    LiveExecutionBlockedError,
    TransportInjectionError,
    check_provider,
    plan_registry_checks,
    to_json,
)
from providers import PROVIDER_IDS, ProviderConfigError

HEALTH_SOURCE = Path(__file__).resolve().parent.parent / "provider_health.py"

CONFIGURED_OPENAI = {
    "provider": "openai_api",
    "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
}


def _step(result, name):
    matches = [s for s in result["steps"] if s["step"] == name]
    assert len(matches) == 1, f"expected exactly one {name} step"
    return matches[0]


# --- plan/result shape across all registered modes -------------------------


def test_check_returns_ordered_steps_for_every_provider_mode():
    configs = [
        CONFIGURED_OPENAI,
        {"provider": "anthropic_api"},
        {"provider": "ollama_local"},
        {"provider": "ollama_cloud"},
        {"provider": "moonshot_kimi_api"},
        {"provider": "deepseek_api"},
        {"provider": "interactive_subscription", "service": "chatgpt"},
        {"provider": "interactive_subscription", "service": "claude"},
    ]
    for config in configs:
        result = check_provider(config)
        assert result["provider"] == config["provider"]
        assert result["live"] is False
        assert [s["step"] for s in result["steps"]] == [
            "validate_config",
            "classify_credential_reference",
            "live_connectivity_probe",
        ]
        assert _step(result, "validate_config")["status"] == "ok"


def test_live_probe_step_is_always_blocked_offline():
    for provider_id in PROVIDER_IDS:
        config = {"provider": provider_id}
        if provider_id == "interactive_subscription":
            config["service"] = "claude"
        probe = _step(check_provider(config), "live_connectivity_probe")
        assert probe["status"] == "blocked_offline"
        assert "operator" in probe["reason"].lower()


# --- classification without reading anything ------------------------------


def test_configured_api_reference_classifies_reference_configured():
    result = check_provider(CONFIGURED_OPENAI)
    assert result["classification"] == "reference_configured"
    step = _step(result, "classify_credential_reference")
    assert step["outcome"] == "reference_configured"
    # Honesty: a configured NAME must never be reported as a working secret.
    rendered = to_json(result).lower()
    for overclaim in ("healthy", "credential valid", "verified", "working"):
        assert overclaim not in rendered


def test_unconfigured_api_providers_classify_unconfigured():
    for provider_id in ("openai_api", "anthropic_api", "ollama_cloud",
                        "moonshot_kimi_api", "deepseek_api"):
        result = check_provider({"provider": provider_id})
        assert result["classification"] == "unconfigured"


def test_ollama_local_classifies_no_auth_local():
    result = check_provider({"provider": "ollama_local"})
    assert result["classification"] == "no_auth_local"


def test_interactive_subscription_classifies_user_owned_session():
    for service in ("chatgpt", "claude"):
        result = check_provider(
            {"provider": "interactive_subscription", "service": service}
        )
        assert result["classification"] == "user_owned_session"
        assert result["service"] == service
        step = _step(result, "classify_credential_reference")
        detail = step["detail"].lower()
        assert "cookie" in detail or "session" in detail


def test_classification_is_identical_with_and_without_env_values(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    with_env = check_provider(CONFIGURED_OPENAI)
    monkeypatch.delenv("OPENAI_API_KEY")
    without_env = check_provider(CONFIGURED_OPENAI)
    assert with_env == without_env
    assert to_json(with_env) == to_json(without_env)


def test_env_values_never_appear_in_results(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    result = check_provider(CONFIGURED_OPENAI)
    assert sentinel not in repr(result)
    assert sentinel not in to_json(result)
    assert sentinel not in to_json(plan_registry_checks())


# --- typed fail-closed boundary --------------------------------------------


@pytest.mark.parametrize("live", [True, 1, "yes", object()])
def test_any_live_execution_request_raises_typed_error(live):
    with pytest.raises(LiveExecutionBlockedError):
        check_provider(CONFIGURED_OPENAI, live=live)


@pytest.mark.parametrize("transport", [object(), lambda url: None, "https", {}])
def test_any_injected_transport_raises_typed_error(transport):
    with pytest.raises(TransportInjectionError):
        check_provider(CONFIGURED_OPENAI, transport=transport)


def test_typed_errors_are_provider_config_errors():
    assert issubclass(LiveExecutionBlockedError, ProviderConfigError)
    assert issubclass(TransportInjectionError, ProviderConfigError)


def test_live_enabled_config_propagates_registry_rejection():
    with pytest.raises(ProviderConfigError):
        check_provider({"provider": "openai_api", "live_enabled": True})


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "unknown_provider"},
        {"provider": []},
        {"provider": "openai_api", "label": "sk-secret-material"},
        {
            "provider": "openai_api",
            "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY", "value": "x"},
        },
        None,
        "openai_api",
    ],
)
def test_unknown_or_secret_shaped_configs_are_rejected(config):
    with pytest.raises(ProviderConfigError):
        check_provider(config)


# --- determinism and redacted serialization --------------------------------


def test_to_json_is_canonical_and_repeatable():
    a = to_json(check_provider(CONFIGURED_OPENAI))
    b = to_json(check_provider(CONFIGURED_OPENAI))
    assert a == b
    parsed = json.loads(a)
    assert parsed["provider"] == "openai_api"
    # canonical: sorted keys at every level
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_to_json_is_stable_across_input_key_ordering():
    reordered = {
        "credential_ref": {"name": "OPENAI_API_KEY", "kind": "env"},
        "provider": "openai_api",
    }
    assert to_json(check_provider(reordered)) == to_json(
        check_provider(CONFIGURED_OPENAI)
    )


def test_to_json_screens_tampered_results_instead_of_emitting():
    result = check_provider(CONFIGURED_OPENAI)
    result["steps"][0]["status"] = "sk-injected-after-check"
    with pytest.raises(ProviderConfigError):
        to_json(result)


def test_check_is_pure_and_does_not_mutate_input():
    payload = copy.deepcopy(CONFIGURED_OPENAI)
    first = check_provider(payload)
    second = check_provider(payload)
    assert payload == CONFIGURED_OPENAI
    assert first == second
    first["steps"][0]["status"] = "tampered"
    assert check_provider(payload)["steps"][0]["status"] == "ok"


# --- registry-wide plan ----------------------------------------------------


def test_registry_plan_covers_every_provider_deterministically():
    plan = plan_registry_checks()
    assert [entry["provider"] for entry in plan] == sorted(PROVIDER_IDS)
    assert to_json(plan) == to_json(plan_registry_checks())
    for entry in plan:
        assert entry["live"] is False
        probe = _step(entry, "live_connectivity_probe")
        assert probe["status"] == "blocked_offline"


# --- structural boundary ---------------------------------------------------


def test_module_source_imports_no_network_or_process_machinery():
    source = HEALTH_SOURCE.read_text(encoding="utf-8")
    for forbidden in (
        "import socket",
        "import urllib",
        "from urllib",
        "import http",
        "from http",
        "import requests",
        "import subprocess",
        "from subprocess",
        "os.environ",
        "getenv",
    ):
        assert forbidden not in source, forbidden


def test_module_exports_no_resolver_shaped_names():
    import provider_health as module

    for name in dir(module):
        lowered = name.lower()
        assert "resolve" not in lowered
        assert "load_credential" not in lowered
        assert "read_secret" not in lowered
