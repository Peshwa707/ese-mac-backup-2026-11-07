"""Offline, deterministic tests for provider request manifests and the
blocked credential-resolver / transport seams.

Pure configuration/artifact behavior only: no network, no credential
resolution, no clocks, no randomness. ``monkeypatch.setenv`` appears only
to PROVE nothing reads environment values.
"""

import copy
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from provider_manifest import (
    CredentialResolutionBlockedError,
    CredentialResolver,
    LiveProviderExecutionBlockedError,
    ManifestStaleError,
    ManifestTamperError,
    ManifestValidationError,
    OfflineCredentialResolver,
    SUPPORTED_OPERATIONS,
    Transport,
    build_health_request,
    execute_manifest,
)
from provider_gate import config_fingerprint
from provider_health import to_json
from providers import PROVIDER_IDS, ProviderConfigError

MANIFEST_SOURCE = Path(__file__).resolve().parent.parent / "provider_manifest.py"

OPENAI_CONFIG = {
    "provider": "openai_api",
    "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
}

API_MODES = (
    "openai_api",
    "anthropic_api",
    "ollama_cloud",
    "moonshot_kimi_api",
    "deepseek_api",
)


def _config_for(provider_id, service="chatgpt"):
    config = {"provider": provider_id}
    if provider_id == "interactive_subscription":
        config["service"] = service
    return config


# --- manifests across all seven modes --------------------------------------


def test_manifests_build_for_all_seven_provider_modes():
    for provider_id in PROVIDER_IDS:
        manifest = build_health_request(_config_for(provider_id))
        assert manifest["provider"] == provider_id
        assert manifest["operation"] == "health_check"
        assert manifest["config_sha256"] == config_fingerprint(_config_for(provider_id))
        assert manifest["manifest_id"]
        assert set(manifest["requires"]) == {"network", "manual_operator"}


def test_supported_operations_is_exactly_health_check():
    assert SUPPORTED_OPERATIONS == ("health_check",)


def test_api_modes_declare_http_with_structural_auth():
    for provider_id in API_MODES:
        manifest = build_health_request(_config_for(provider_id))
        assert manifest["protocol_family"] == "http"
        http = manifest["http"]
        assert http["method"] == "GET"
        assert http["route"].startswith("/")
        assert http["host"]
        assert manifest["requires"]["network"] is True
        assert manifest["requires"]["manual_operator"] is False
    openai = build_health_request(OPENAI_CONFIG)
    assert openai["http"]["auth"] == {"scheme": "bearer", "header": "Authorization"}


def test_no_bearer_literal_or_secret_shape_anywhere_in_manifests():
    for provider_id in PROVIDER_IDS:
        rendered = to_json(build_health_request(_config_for(provider_id)))
        assert "Bearer " not in rendered
        assert "sk-" not in rendered


def test_ollama_local_is_http_no_auth_but_still_networked():
    manifest = build_health_request({"provider": "ollama_local"})
    assert manifest["protocol_family"] == "http"
    assert manifest["http"]["auth"] == {"scheme": "none"}
    assert manifest["requires"]["network"] is True
    assert manifest["credential"]["posture"] == "none_required"
    assert manifest["credential"]["ref"] is None


# --- subscription modes: manual-only, no automated request -----------------


def test_subscription_manifests_are_manual_session_only():
    for service in ("chatgpt", "claude"):
        manifest = build_health_request(_config_for("interactive_subscription", service))
        assert manifest["protocol_family"] == "manual_session"
        assert "http" not in manifest
        assert manifest["requires"]["network"] is False
        assert manifest["requires"]["manual_operator"] is True
        assert manifest["service"] == service
        instruction = manifest["manual_instruction"].lower()
        assert "automat" in instruction  # no automation / never automated
        assert manifest["credential"]["posture"] == "user_owned_session"
        assert manifest["credential"]["ref"] is None


def test_api_modes_are_distinct_from_subscription_sessions():
    openai_api = build_health_request(OPENAI_CONFIG)
    chatgpt_sub = build_health_request(_config_for("interactive_subscription", "chatgpt"))
    anthropic_api = build_health_request({"provider": "anthropic_api"})
    claude_sub = build_health_request(_config_for("interactive_subscription", "claude"))
    assert openai_api["protocol_family"] == "http"
    assert chatgpt_sub["protocol_family"] == "manual_session"
    assert anthropic_api["protocol_family"] == "http"
    assert claude_sub["protocol_family"] == "manual_session"
    assert openai_api["manifest_id"] != chatgpt_sub["manifest_id"]
    assert anthropic_api["manifest_id"] != claude_sub["manifest_id"]


# --- credential metadata: references only ----------------------------------


def test_credential_block_carries_reference_metadata_only():
    configured = build_health_request(OPENAI_CONFIG)
    assert configured["credential"]["posture"] == "reference_configured"
    assert configured["credential"]["ref"] == {"kind": "env", "name": "OPENAI_API_KEY"}
    assert configured["credential"]["default_env_var"] == "OPENAI_API_KEY"
    unconfigured = build_health_request({"provider": "deepseek_api"})
    assert unconfigured["credential"]["posture"] == "unconfigured"
    assert unconfigured["credential"]["ref"] is None
    assert unconfigured["credential"]["default_env_var"] == "DEEPSEEK_API_KEY"


def test_manifests_never_read_environment(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    with_env = build_health_request(OPENAI_CONFIG)
    monkeypatch.delenv("OPENAI_API_KEY")
    without_env = build_health_request(OPENAI_CONFIG)
    assert with_env == without_env
    assert sentinel not in to_json(with_env)
    assert sentinel not in repr(with_env)


# --- determinism and identity ----------------------------------------------


def test_manifest_id_is_content_derived_and_deterministic():
    a = build_health_request(OPENAI_CONFIG)
    b = build_health_request(OPENAI_CONFIG)
    assert a == b
    assert to_json(a) == to_json(b)
    body = {k: v for k, v in a.items() if k != "manifest_id"}
    assert a["manifest_id"] == hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()


def test_manifest_stable_across_config_key_order():
    reordered = {
        "credential_ref": {"name": "OPENAI_API_KEY", "kind": "env"},
        "provider": "openai_api",
    }
    assert to_json(build_health_request(reordered)) == to_json(
        build_health_request(OPENAI_CONFIG)
    )


def test_manifest_json_is_canonical():
    parsed = json.loads(to_json(build_health_request(OPENAI_CONFIG)))
    assert list(parsed.keys()) == sorted(parsed.keys())


# --- build-time rejection (fail closed) ------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "unknown_provider"},
        {"provider": []},
        {"provider": "openai_api", "live_enabled": True},
        {"provider": "openai_api", "label": "sk-secret-material"},
        None,
        "openai_api",
        {},
    ],
)
def test_invalid_configs_are_rejected_at_build(config):
    with pytest.raises(ProviderConfigError):
        build_health_request(config)


def test_unsupported_operations_are_rejected():
    from provider_manifest import build_request

    for operation in ("chat", "completion", "", None, "HEALTH_CHECK"):
        with pytest.raises(ManifestValidationError):
            build_request(OPENAI_CONFIG, operation=operation)


# --- credential resolver seam ----------------------------------------------


def test_offline_resolver_always_raises_blocked_for_valid_refs():
    resolver = OfflineCredentialResolver()
    for ref in (
        {"kind": "env", "name": "OPENAI_API_KEY"},
        {"kind": "keychain", "service": "com.neurologic.ese", "account": "anthropic-api-key"},
    ):
        with pytest.raises(CredentialResolutionBlockedError):
            resolver.resolve(ref)


@pytest.mark.parametrize(
    "ref",
    [
        None,
        "OPENAI_API_KEY",
        {},
        {"kind": "file", "name": "X"},
        {"kind": "env", "name": "OPENAI_API_KEY", "value": "x"},
        {"kind": "env", "name": "sk-secret"},
    ],
)
def test_offline_resolver_rejects_malformed_refs(ref):
    with pytest.raises(ProviderConfigError):
        OfflineCredentialResolver().resolve(ref)


def test_resolver_is_a_typed_interface():
    assert inspect.isabstract(CredentialResolver)
    assert issubclass(OfflineCredentialResolver, CredentialResolver)


def test_resolver_never_returns_a_value_even_with_env_set(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    with pytest.raises(CredentialResolutionBlockedError) as excinfo:
        OfflineCredentialResolver().resolve({"kind": "env", "name": "OPENAI_API_KEY"})
    assert sentinel not in str(excinfo.value)


# --- transport seam and execution ------------------------------------------


class _RogueTransport(Transport):
    def send(self, manifest):  # pragma: no cover - must never be reached
        raise AssertionError("transport must never be invoked offline")


def test_transport_is_a_typed_interface():
    assert inspect.isabstract(Transport)


def test_valid_manifest_execution_is_blocked():
    manifest = build_health_request(OPENAI_CONFIG)
    with pytest.raises(LiveProviderExecutionBlockedError):
        execute_manifest(manifest, OPENAI_CONFIG)


def test_replay_refusal_is_stateless_every_attempt_blocks():
    manifest = build_health_request(OPENAI_CONFIG)
    for _ in range(3):
        with pytest.raises(LiveProviderExecutionBlockedError):
            execute_manifest(manifest, OPENAI_CONFIG)


def test_injected_transport_is_rejected():
    manifest = build_health_request(OPENAI_CONFIG)
    for transport in (_RogueTransport(), object(), "https", lambda m: None):
        with pytest.raises(LiveProviderExecutionBlockedError):
            execute_manifest(manifest, OPENAI_CONFIG, transport=transport)


def test_injected_resolver_is_rejected():
    manifest = build_health_request(OPENAI_CONFIG)
    for resolver in (OfflineCredentialResolver(), object()):
        with pytest.raises(LiveProviderExecutionBlockedError):
            execute_manifest(manifest, OPENAI_CONFIG, resolver=resolver)


@pytest.mark.parametrize("live", [True, 1, "yes"])
def test_live_request_is_rejected(live):
    manifest = build_health_request(OPENAI_CONFIG)
    with pytest.raises(LiveProviderExecutionBlockedError):
        execute_manifest(manifest, OPENAI_CONFIG, live=live)


def test_execution_without_a_manifest_is_impossible():
    for bogus in (None, {}, "manifest", 42, {"manifest_id": "x"}):
        with pytest.raises(ManifestValidationError):
            execute_manifest(bogus, OPENAI_CONFIG)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "chat"),
        ("provider", "anthropic_api"),
        ("config_sha256", "0" * 64),
        ("protocol_family", "manual_session"),
    ],
)
def test_tampered_manifest_fields_are_detected(field, value):
    manifest = dict(build_health_request(OPENAI_CONFIG))
    manifest[field] = value
    with pytest.raises((ManifestTamperError, ManifestValidationError, ManifestStaleError)):
        execute_manifest(manifest, OPENAI_CONFIG)


def test_config_drift_makes_manifest_stale():
    manifest = build_health_request(OPENAI_CONFIG)
    drifted = {"provider": "openai_api"}  # reference removed -> different hash
    with pytest.raises(ManifestStaleError):
        execute_manifest(manifest, drifted)


def test_unknown_manifest_version_is_stale():
    manifest = dict(build_health_request(OPENAI_CONFIG))
    manifest["manifest_version"] = 99
    with pytest.raises((ManifestStaleError, ManifestTamperError)):
        execute_manifest(manifest, OPENAI_CONFIG)


def test_all_new_errors_are_provider_config_errors():
    for exc in (
        ManifestValidationError,
        ManifestTamperError,
        ManifestStaleError,
        CredentialResolutionBlockedError,
        LiveProviderExecutionBlockedError,
    ):
        assert issubclass(exc, ProviderConfigError)


# --- purity and structural no-I/O ------------------------------------------


def test_build_and_execute_do_not_mutate_inputs():
    config = copy.deepcopy(OPENAI_CONFIG)
    manifest = build_health_request(config)
    snapshot = copy.deepcopy(manifest)
    with pytest.raises(LiveProviderExecutionBlockedError):
        execute_manifest(manifest, config)
    assert config == OPENAI_CONFIG
    assert manifest == snapshot


def test_module_source_imports_no_network_or_process_machinery():
    source = MANIFEST_SOURCE.read_text(encoding="utf-8")
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
        "import time",
        "import random",
        "datetime",
    ):
        assert forbidden not in source, forbidden
