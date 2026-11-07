"""Offline, deterministic tests for the per-provider/per-run operator gate.

Everything here is pure configuration/artifact behavior: no network, no
credential resolution, no clocks, no randomness. ``monkeypatch.setenv``
appears only to PROVE the gate never reads environment values.
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from provider_gate import (
    GateReusedError,
    GateStaleError,
    GateTamperError,
    GateValidationError,
    LiveExecutionNotImplementedError,
    SUPPORTED_ACTIONS,
    config_fingerprint,
    execute_gated_action,
    issue_gate,
)
from provider_health import TransportInjectionError, to_json
from providers import PROVIDER_IDS, ProviderConfigError

GATE_SOURCE = Path(__file__).resolve().parent.parent / "provider_gate.py"

RUN_ID_A = "abc123def456"
RUN_ID_B = "fedcba654321"

OPENAI_CONFIG = {
    "provider": "openai_api",
    "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
}


def _ack(provider_id, action="health_check"):
    return {
        "confirmed": True,
        "statement": (
            f"I authorize a single {action} against provider {provider_id} "
            "for this run only"
        ),
    }


_DEFAULT_ACK = object()  # sentinel: ack=None must reach issue_gate as None


def _issue(config=None, run_id=RUN_ID_A, action="health_check", ack=_DEFAULT_ACK):
    config = OPENAI_CONFIG if config is None else config
    return issue_gate(
        config,
        run_id=run_id,
        action=action,
        operator_ack=_ack(config["provider"], action) if ack is _DEFAULT_ACK else ack,
    )


# --- issuance across all seven modes ---------------------------------------


def test_gates_issue_for_all_seven_provider_modes():
    for provider_id in PROVIDER_IDS:
        config = {"provider": provider_id}
        if provider_id == "interactive_subscription":
            config["service"] = "chatgpt"
        gate = _issue(config=config)
        assert gate["provider"] == provider_id
        assert gate["run_id"] == RUN_ID_A
        assert gate["action"] == "health_check"
        assert gate["config_sha256"] == config_fingerprint(config)
        assert gate["gate_id"]


def test_supported_actions_is_exactly_health_check():
    assert SUPPORTED_ACTIONS == ("health_check",)


def test_interactive_gates_carry_manual_operator_note():
    for service in ("chatgpt", "claude"):
        gate = _issue(
            config={"provider": "interactive_subscription", "service": service}
        )
        note = gate["execution_note"].lower()
        assert "manual" in note
        assert "automat" in note  # no automation / never automated


# --- binding: config hash, identity, determinism ---------------------------


def test_config_fingerprint_is_sha256_of_canonical_validated_json():
    expected = hashlib.sha256(
        to_json(
            __import__("providers").validate_provider_config(OPENAI_CONFIG)
        ).encode("utf-8")
    ).hexdigest()
    assert config_fingerprint(OPENAI_CONFIG) == expected


def test_gate_id_is_content_derived_and_deterministic():
    gate_a = _issue()
    gate_b = _issue()
    assert gate_a == gate_b
    assert to_json(gate_a) == to_json(gate_b)
    body = {k: v for k, v in gate_a.items() if k != "gate_id"}
    assert gate_a["gate_id"] == hashlib.sha256(
        to_json(body).encode("utf-8")
    ).hexdigest()


def test_gate_serialization_stable_across_config_key_order():
    reordered = {
        "credential_ref": {"name": "OPENAI_API_KEY", "kind": "env"},
        "provider": "openai_api",
    }
    assert to_json(_issue(config=reordered)) == to_json(_issue())


def test_gate_never_reads_environment(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    with_env = _issue()
    monkeypatch.delenv("OPENAI_API_KEY")
    without_env = _issue()
    assert with_env == without_env
    assert sentinel not in to_json(with_env)
    assert sentinel not in repr(with_env)


# --- issuance failure paths (fail closed) ----------------------------------


@pytest.mark.parametrize("run_id", ["", "short", "UPPERCASE123", "abc123def45!", "a" * 13, None, 12])
def test_malformed_run_ids_are_rejected(run_id):
    with pytest.raises(GateValidationError):
        _issue(run_id=run_id)


@pytest.mark.parametrize("action", ["chat", "screen", "extract", "", None, "HEALTH_CHECK"])
def test_unsupported_actions_are_rejected(action):
    with pytest.raises(ProviderConfigError):
        _issue(action=action)


@pytest.mark.parametrize(
    "ack",
    [
        None,
        {},
        {"confirmed": True},  # missing statement
        {"statement": "I authorize health_check on openai_api"},  # missing confirmed
        {"confirmed": False, "statement": "I authorize health_check on openai_api"},
        {"confirmed": "true", "statement": "I authorize health_check on openai_api"},
        {"confirmed": 1, "statement": "I authorize health_check on openai_api"},
        {"confirmed": True, "statement": ""},
        {"confirmed": True, "statement": "   "},
        {"confirmed": True, "statement": "yes"},  # names neither provider nor action
        {"confirmed": True, "statement": "I authorize health_check"},  # no provider
        {"confirmed": True, "statement": "I authorize openai_api"},  # no action
        {"confirmed": True, "statement": "I authorize health_check on openai_api", "extra": 1},
    ],
)
def test_missing_false_or_malformed_acknowledgment_is_rejected(ack):
    with pytest.raises(GateValidationError):
        _issue(ack=ack)


def test_secret_shaped_material_in_acknowledgment_is_rejected():
    with pytest.raises(ProviderConfigError):
        _issue(
            ack={
                "confirmed": True,
                "statement": "I authorize health_check on openai_api sk-secret123",
            }
        )


def test_invalid_configs_propagate_registry_rejection():
    with pytest.raises(ProviderConfigError):
        _issue(config={"provider": "unknown_provider"})
    with pytest.raises(ProviderConfigError):
        _issue(config={"provider": "openai_api", "live_enabled": True})


# --- execution seam: verification then typed block -------------------------


def test_valid_gate_executes_into_typed_not_implemented_block():
    gate = _issue()
    used: set = set()
    with pytest.raises(LiveExecutionNotImplementedError) as excinfo:
        execute_gated_action(
            gate, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check", used_gates=used
        )
    message = str(excinfo.value).lower()
    assert "not implemented" in message or "blocked" in message


def test_execution_seam_requires_explicit_used_gates_ledger():
    gate = _issue()
    with pytest.raises(TypeError):
        execute_gated_action(gate, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check")


def test_gate_burns_on_first_attempt_and_reuse_is_rejected():
    gate = _issue()
    used: set = set()
    with pytest.raises(LiveExecutionNotImplementedError):
        execute_gated_action(
            gate, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check", used_gates=used
        )
    assert gate["gate_id"] in used
    with pytest.raises(GateReusedError):
        execute_gated_action(
            gate, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check", used_gates=used
        )


def test_run_mismatch_fails_closed():
    gate = _issue(run_id=RUN_ID_A)
    with pytest.raises(GateValidationError):
        execute_gated_action(
            gate, OPENAI_CONFIG, run_id=RUN_ID_B, action="health_check", used_gates=set()
        )


def test_provider_config_mismatch_fails_closed():
    gate = _issue()
    other = {"provider": "deepseek_api"}
    with pytest.raises((GateValidationError, GateStaleError)):
        execute_gated_action(
            gate, other, run_id=RUN_ID_A, action="health_check", used_gates=set()
        )


def test_config_drift_makes_gate_stale():
    gate = _issue()  # bound to the configured-reference form
    drifted = {"provider": "openai_api"}  # reference removed → different hash
    with pytest.raises(GateStaleError):
        execute_gated_action(
            gate, drifted, run_id=RUN_ID_A, action="health_check", used_gates=set()
        )


def test_unknown_gate_version_is_stale():
    gate = dict(_issue())
    gate["gate_version"] = 99
    with pytest.raises((GateStaleError, GateTamperError)):
        execute_gated_action(
            gate, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check", used_gates=set()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", "chat"),
        ("provider", "anthropic_api"),
        ("run_id", RUN_ID_B),
        ("config_sha256", "0" * 64),
        ("operator_ack", {"confirmed": True, "statement": "tampered health_check openai_api"}),
    ],
)
def test_tampered_gate_fields_are_detected(field, value):
    gate = dict(_issue())
    gate[field] = value
    with pytest.raises((GateTamperError, GateValidationError, GateStaleError)):
        execute_gated_action(
            gate, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check", used_gates=set()
        )


def test_execution_without_a_gate_is_impossible():
    for bogus in (None, {}, "gate", 42, {"gate_id": "x"}):
        with pytest.raises(GateValidationError):
            execute_gated_action(
                bogus, OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check", used_gates=set()
            )


@pytest.mark.parametrize("transport", [object(), lambda url: None, "https", {}])
def test_injected_transport_is_rejected_before_anything_else(transport):
    with pytest.raises(TransportInjectionError):
        execute_gated_action(
            _issue(), OPENAI_CONFIG, run_id=RUN_ID_A, action="health_check",
            used_gates=set(), transport=transport,
        )


def test_all_gate_errors_are_provider_config_errors():
    for exc in (
        GateValidationError,
        GateStaleError,
        GateTamperError,
        GateReusedError,
        LiveExecutionNotImplementedError,
    ):
        assert issubclass(exc, ProviderConfigError)


# --- purity and structural no-I/O ------------------------------------------


def test_issue_and_execute_do_not_mutate_inputs():
    config = copy.deepcopy(OPENAI_CONFIG)
    gate = issue_gate(
        config, run_id=RUN_ID_A, action="health_check",
        operator_ack=_ack("openai_api"),
    )
    snapshot_gate = copy.deepcopy(gate)
    with pytest.raises(LiveExecutionNotImplementedError):
        execute_gated_action(
            gate, config, run_id=RUN_ID_A, action="health_check", used_gates=set()
        )
    assert config == OPENAI_CONFIG
    assert gate == snapshot_gate


def test_gate_json_round_trips_canonically():
    gate = _issue()
    rendered = to_json(gate)
    assert json.loads(rendered) == json.loads(to_json(_issue()))
    parsed = json.loads(rendered)
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_module_source_imports_no_network_or_process_machinery():
    source = GATE_SOURCE.read_text(encoding="utf-8")
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


def test_module_exports_no_resolver_shaped_names():
    import provider_gate as module

    for name in dir(module):
        lowered = name.lower()
        assert "resolve" not in lowered
        assert "load_credential" not in lowered
        assert "read_secret" not in lowered
