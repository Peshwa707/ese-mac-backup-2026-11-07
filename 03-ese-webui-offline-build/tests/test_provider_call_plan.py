"""Offline, deterministic tests for gate-bound adapter call plans and the
blocked plan-execution seam.

Pure configuration/artifact behavior only: no network, no credential
resolution, no clocks, no randomness. ``monkeypatch.setenv`` appears only
to PROVE nothing reads environment values.
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from provider_call_plan import (
    CallPlanStaleError,
    CallPlanTamperError,
    CallPlanValidationError,
    FUTURE_EXECUTOR,
    PLAN_VERSION,
    build_call_plan,
    execute_call_plan,
    verify_gate_binding,
)
from provider_gate import (
    GateStaleError,
    GateTamperError,
    GateValidationError,
    LiveExecutionNotImplementedError,
    config_fingerprint,
    execute_gated_action,
    issue_gate,
)
from provider_health import to_json
from provider_manifest import (
    LiveProviderExecutionBlockedError,
    ManifestStaleError,
    ManifestTamperError,
    ManifestValidationError,
    OfflineCredentialResolver,
    build_health_request,
)
from providers import PROVIDER_IDS, ProviderConfigError

PLAN_SOURCE = Path(__file__).resolve().parent.parent / "provider_call_plan.py"

RUN = "abc123def456"
ACTION = "health_check"

OPENAI_CONFIG = {
    "provider": "openai_api",
    "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
}

API_MODES = (
    "openai_api",
    "anthropic_api",
    "ollama_local",
    "ollama_cloud",
    "moonshot_kimi_api",
    "deepseek_api",
)


def _config_for(provider_id, service="chatgpt"):
    config = {"provider": provider_id}
    if provider_id == "interactive_subscription":
        config["service"] = service
    return config


def _ack_for(provider_id, action=ACTION):
    return {
        "confirmed": True,
        "statement": (
            f"I authorize a single {action} attempt against {provider_id} "
            "for this run"
        ),
    }


def _plan_inputs(provider_id, service="chatgpt"):
    config = _config_for(provider_id, service)
    manifest = build_health_request(config)
    gate = issue_gate(
        config, run_id=RUN, action=ACTION, operator_ack=_ack_for(provider_id)
    )
    return config, manifest, gate


def _plan_for(provider_id, service="chatgpt"):
    config, manifest, gate = _plan_inputs(provider_id, service)
    plan = build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
    return config, gate, plan


# --- plans across all seven modes -------------------------------------------


def test_plans_build_for_all_seven_provider_modes():
    for provider_id in PROVIDER_IDS:
        config, manifest, gate = _plan_inputs(provider_id)
        plan = build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
        assert plan["plan_version"] == PLAN_VERSION
        assert plan["provider"] == provider_id
        assert plan["run_id"] == RUN
        assert plan["action"] == ACTION
        assert plan["gate_id"] == gate["gate_id"]
        assert plan["manifest_id"] == manifest["manifest_id"]
        assert plan["config_sha256"] == config_fingerprint(config)
        assert plan["plan_id"]
        execution = plan["execution"]
        assert execution["operator_gate_required"] is True
        assert execution["executable_in_this_build"] is False


def test_api_plans_carry_exactly_the_manifest_request_shape():
    for provider_id in API_MODES:
        config, manifest, gate = _plan_inputs(provider_id)
        plan = build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
        assert plan["mode"] == "api_http"
        assert plan["request"] == manifest["http"]
        assert plan["request"] is not manifest["http"]  # copied, not aliased
        assert plan["execution"]["executor"] == FUTURE_EXECUTOR


def test_subscription_plans_are_manual_operator_only():
    for service in ("chatgpt", "claude"):
        config, manifest, gate = _plan_inputs("interactive_subscription", service)
        plan = build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
        assert plan["mode"] == "manual_operator"
        assert plan["service"] == service
        assert plan["execution"]["executor"] == "human_operator"
        instruction = plan["manual_instruction"].lower()
        assert "automat" in instruction  # never automates


def test_subscription_plans_make_automation_unrepresentable():
    _, _, plan = _plan_for("interactive_subscription")
    # No HTTP request, transport, or automation surface exists in the
    # artifact at all — asserted over the FULL key set, not just spot keys.
    assert set(plan) == {
        "plan_version",
        "provider",
        "access",
        "action",
        "run_id",
        "gate_id",
        "manifest_id",
        "config_sha256",
        "mode",
        "execution",
        "service",
        "manual_instruction",
        "plan_id",
    }
    for forbidden in ("request", "http", "transport", "url", "method"):
        assert forbidden not in plan


# --- planning verifies without burning the gate -----------------------------


def test_planning_does_not_burn_the_gate():
    config, manifest, gate = _plan_inputs("openai_api")
    for _ in range(3):  # planning is repeatable — no single-use ledger involved
        build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
    # The gate is still fresh: the burning execution seam accepts it fully
    # and reaches its terminal block (NOT GateReusedError).
    with pytest.raises(LiveExecutionNotImplementedError):
        execute_gated_action(
            gate, config, run_id=RUN, action=ACTION, used_gates=set()
        )


def test_verify_gate_binding_returns_validated_config_and_is_pure():
    config, _, gate = _plan_inputs("openai_api")
    snapshot = copy.deepcopy(gate)
    validated = verify_gate_binding(gate, config, run_id=RUN, action=ACTION)
    assert validated["provider"] == "openai_api"
    assert gate == snapshot


# --- fail-closed binding and integrity checks --------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "anthropic_api"),
        ("run_id", "000000000000"),
        ("action", "chat"),
        ("config_sha256", "0" * 64),
        ("single_use", False),
        ("operator_ack", {"confirmed": True, "statement": "yes"}),
    ],
)
def test_tampered_gate_fields_are_rejected(field, value):
    config, manifest, gate = _plan_inputs("openai_api")
    tampered = dict(gate)
    tampered[field] = value
    with pytest.raises((GateTamperError, GateValidationError, GateStaleError)):
        build_call_plan(config, manifest, tampered, run_id=RUN, action=ACTION)


def test_gate_bound_to_a_different_run_is_rejected():
    config, manifest, _ = _plan_inputs("openai_api")
    other = issue_gate(
        config,
        run_id="fedcba987654",
        action=ACTION,
        operator_ack=_ack_for("openai_api"),
    )
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, other, run_id=RUN, action=ACTION)


def test_gate_for_a_different_provider_is_rejected():
    config, manifest, _ = _plan_inputs("openai_api")
    other_config = {"provider": "deepseek_api"}
    other_gate = issue_gate(
        other_config,
        run_id=RUN,
        action=ACTION,
        operator_ack=_ack_for("deepseek_api"),
    )
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, other_gate, run_id=RUN, action=ACTION)


def test_config_drift_since_gate_issuance_is_stale():
    config, _, gate = _plan_inputs("openai_api")
    drifted = {
        "provider": "openai_api",
        "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
        "label": "renamed",
    }
    manifest = build_health_request(drifted)
    with pytest.raises(GateStaleError):
        build_call_plan(drifted, manifest, gate, run_id=RUN, action=ACTION)


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "anthropic_api"),
        ("operation", "chat"),
        ("config_sha256", "0" * 64),
        ("protocol_family", "manual_session"),
    ],
)
def test_tampered_manifest_fields_are_rejected(field, value):
    config, manifest, gate = _plan_inputs("openai_api")
    tampered = dict(manifest)
    tampered[field] = value
    with pytest.raises(
        (ManifestTamperError, ManifestValidationError, ManifestStaleError)
    ):
        build_call_plan(config, tampered, gate, run_id=RUN, action=ACTION)


def test_manifest_built_for_a_different_config_is_rejected():
    gate = issue_gate(
        OPENAI_CONFIG, run_id=RUN, action=ACTION, operator_ack=_ack_for("openai_api")
    )
    foreign_manifest = build_health_request({"provider": "openai_api"})  # no ref
    with pytest.raises(ManifestStaleError):
        build_call_plan(
            OPENAI_CONFIG, foreign_manifest, gate, run_id=RUN, action=ACTION
        )


@pytest.mark.parametrize("run_id", [None, "", "SHORT", "g" * 12, "abc123def45"])
def test_malformed_run_ids_are_rejected(run_id):
    config, manifest, gate = _plan_inputs("openai_api")
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, gate, run_id=run_id, action=ACTION)


@pytest.mark.parametrize("action", [None, "", "chat", "HEALTH_CHECK"])
def test_unsupported_actions_are_rejected(action):
    config, manifest, gate = _plan_inputs("openai_api")
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, gate, run_id=RUN, action=action)


@pytest.mark.parametrize("bogus", [None, {}, "gate", 42, ["gate"]])
def test_planning_without_a_real_gate_is_impossible(bogus):
    config, manifest, _ = _plan_inputs("openai_api")
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, bogus, run_id=RUN, action=ACTION)


@pytest.mark.parametrize("bogus", [None, {}, "manifest", 42])
def test_planning_without_a_real_manifest_is_impossible(bogus):
    config, _, gate = _plan_inputs("openai_api")
    with pytest.raises(ManifestValidationError):
        build_call_plan(config, bogus, gate, run_id=RUN, action=ACTION)


# --- forged-but-self-consistent artifacts -------------------------------------


def _reforge(artifact, id_key, **overrides):
    """Rebuild an artifact with overrides and a RECOMPUTED content hash.

    This models an adversary who can compute sha256 — the hash is a
    checksum, not a MAC, so semantic cross-field checks must reject what
    the hash alone cannot.
    """
    body = {k: v for k, v in artifact.items() if k != id_key}
    for key, value in overrides.items():
        if value is _ABSENT:
            body.pop(key, None)
        else:
            body[key] = value
    forged = dict(body)
    forged[id_key] = hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()
    return forged


_ABSENT = object()


def test_forged_subscription_manifest_carrying_http_is_rejected():
    config = _config_for("interactive_subscription")
    manifest = build_health_request(config)
    forged = _reforge(
        manifest,
        "manifest_id",
        http={"method": "GET", "route": "/v1/x", "host": "example.com"},
    )
    gate = issue_gate(
        config,
        run_id=RUN,
        action=ACTION,
        operator_ack=_ack_for("interactive_subscription"),
    )
    with pytest.raises(ManifestValidationError):
        build_call_plan(config, forged, gate, run_id=RUN, action=ACTION)


def test_forged_api_manifest_without_http_is_rejected_typed():
    config, manifest, gate = _plan_inputs("openai_api")
    forged = _reforge(manifest, "manifest_id", http=_ABSENT)
    with pytest.raises(ManifestValidationError):  # typed, never a raw KeyError
        build_call_plan(config, forged, gate, run_id=RUN, action=ACTION)


def test_forged_manifest_injecting_fields_into_http_is_rejected():
    # A hash-self-consistent forgery adds a session field inside the http
    # block; it must be rejected, never copied into the plan's request.
    config, manifest, gate = _plan_inputs("openai_api")
    forged = _reforge(
        manifest,
        "manifest_id",
        http={**manifest["http"], "cookie": "opaque-session-value"},
    )
    with pytest.raises(ManifestValidationError):
        build_call_plan(config, forged, gate, run_id=RUN, action=ACTION)


def test_forged_manifest_altering_the_request_target_is_rejected():
    config, manifest, gate = _plan_inputs("openai_api")
    forged = _reforge(
        manifest,
        "manifest_id",
        http={**manifest["http"], "host": "exfil.example.com"},
    )
    with pytest.raises(ManifestValidationError):
        build_call_plan(config, forged, gate, run_id=RUN, action=ACTION)


def test_non_json_values_in_a_gate_fail_typed_not_raw():
    config, manifest, gate = _plan_inputs("openai_api")
    tampered = dict(gate)
    tampered["operator_ack"] = {"confirmed": True, "statement": object()}
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, tampered, run_id=RUN, action=ACTION)


def test_hash_consistent_non_string_gate_provider_fails_typed_not_raw():
    config, manifest, gate = _plan_inputs("openai_api")
    forged = _reforge(gate, "gate_id", provider=1)
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, forged, run_id=RUN, action=ACTION)


def test_hash_consistent_non_string_gate_action_fails_typed_not_raw():
    config, manifest, gate = _plan_inputs("openai_api")
    forged = _reforge(gate, "gate_id", action=[])
    with pytest.raises(GateValidationError):
        build_call_plan(config, manifest, forged, run_id=RUN, action=ACTION)


def test_non_json_values_in_a_plan_fail_typed_not_raw():
    config, gate, plan = _plan_for("openai_api")
    tampered = dict(plan)
    tampered["execution"] = {**plan["execution"], "note": object()}
    with pytest.raises(CallPlanValidationError):
        execute_call_plan(tampered, config, gate)


def test_forged_manifest_access_mismatch_is_rejected():
    config, manifest, gate = _plan_inputs("openai_api")
    forged = _reforge(manifest, "manifest_id", access="local_no_auth")
    with pytest.raises(ManifestValidationError):
        build_call_plan(config, forged, gate, run_id=RUN, action=ACTION)


def test_planner_and_burning_executor_agree_on_every_gate_mutation():
    """Differential check: the non-burning verifier is no weaker than the
    burning one. For a corpus of valid, tampered, and forged-but-hash-
    consistent gates, build_call_plan accepts a gate iff
    provider_gate.execute_gated_action would accept it (i.e. reach its
    terminal LiveExecutionNotImplementedError)."""
    config, manifest, gate = _plan_inputs("openai_api")

    corpus = [gate]
    # Tampered without recomputing the hash:
    for field, value in [
        ("provider", "deepseek_api"),
        ("run_id", "000000000000"),
        ("action", "chat"),
        ("config_sha256", "0" * 64),
        ("single_use", False),
        ("operator_ack", {"confirmed": True, "statement": "yes"}),
    ]:
        tampered = dict(gate)
        tampered[field] = value
        corpus.append(tampered)
    # Forged WITH a recomputed, self-consistent hash:
    corpus.extend(
        [
            _reforge(gate, "gate_id", operator_ack={"confirmed": True, "statement": "yes"}),
            _reforge(gate, "gate_id", run_id="000000000000"),
            _reforge(gate, "gate_id", single_use=False),
            _reforge(gate, "gate_id", provider="deepseek_api"),
            _reforge(gate, "gate_id", config_sha256="0" * 64),
            _reforge(gate, "gate_id", gate_version=99),
            _reforge(gate, "gate_id", extra_field=True),
            _reforge(gate, "gate_id", operator_ack=_ABSENT),
        ]
    )

    for candidate in corpus:
        try:
            build_call_plan(config, manifest, candidate, run_id=RUN, action=ACTION)
            planner_accepts = True
        except ProviderConfigError:
            planner_accepts = False
        try:
            execute_gated_action(
                candidate, config, run_id=RUN, action=ACTION, used_gates=set()
            )
            raise AssertionError("executor must never return")
        except LiveExecutionNotImplementedError:
            executor_accepts = True
        except ProviderConfigError:
            executor_accepts = False
        assert planner_accepts == executor_accepts, candidate


# --- determinism, identity, and redaction ------------------------------------


def test_plan_id_is_content_derived_and_plans_are_byte_deterministic():
    _, _, a = _plan_for("openai_api")
    _, _, b = _plan_for("openai_api")
    assert a == b
    assert to_json(a) == to_json(b)
    body = {k: v for k, v in a.items() if k != "plan_id"}
    assert a["plan_id"] == hashlib.sha256(to_json(body).encode("utf-8")).hexdigest()


def test_plans_are_byte_identical_across_input_key_order():
    reordered_config = {
        "credential_ref": {"name": "OPENAI_API_KEY", "kind": "env"},
        "provider": "openai_api",
    }
    manifest_a = build_health_request(OPENAI_CONFIG)
    manifest_b = build_health_request(reordered_config)
    ack = _ack_for("openai_api")
    gate_a = issue_gate(OPENAI_CONFIG, run_id=RUN, action=ACTION, operator_ack=ack)
    gate_b = issue_gate(reordered_config, run_id=RUN, action=ACTION, operator_ack=ack)
    plan_a = build_call_plan(OPENAI_CONFIG, manifest_a, gate_a, run_id=RUN, action=ACTION)
    plan_b = build_call_plan(reordered_config, manifest_b, gate_b, run_id=RUN, action=ACTION)
    assert to_json(plan_a) == to_json(plan_b)


def test_plan_json_is_canonical():
    _, _, plan = _plan_for("openai_api")
    parsed = json.loads(to_json(plan))
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_plans_never_read_environment(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    config, manifest, gate = _plan_inputs("openai_api")
    with_env = build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
    monkeypatch.delenv("OPENAI_API_KEY")
    without_env = build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
    assert with_env == without_env
    assert sentinel not in to_json(with_env)
    assert sentinel not in repr(with_env)


def test_no_secret_or_session_material_in_any_plan():
    for provider_id in PROVIDER_IDS:
        _, _, plan = _plan_for(provider_id)
        rendered = to_json(plan)
        assert "Bearer " not in rendered
        assert "sk-" not in rendered
        for forbidden_key in ("cookie", "session_token", "browser_profile", "value"):
            assert forbidden_key not in plan


def test_build_call_plan_mutates_none_of_its_inputs():
    config, manifest, gate = _plan_inputs("openai_api")
    config_snap = copy.deepcopy(config)
    manifest_snap = copy.deepcopy(manifest)
    gate_snap = copy.deepcopy(gate)
    build_call_plan(config, manifest, gate, run_id=RUN, action=ACTION)
    assert config == config_snap
    assert manifest == manifest_snap
    assert gate == gate_snap


# --- the blocked execution seam ----------------------------------------------


def test_every_valid_plan_execution_is_blocked_for_all_seven_modes():
    for provider_id in PROVIDER_IDS:
        config, gate, plan = _plan_for(provider_id)
        with pytest.raises(LiveProviderExecutionBlockedError):
            execute_call_plan(plan, config, gate)


def test_seam_is_stateless_every_attempt_blocks_identically():
    config, gate, plan = _plan_for("openai_api")
    messages = set()
    for _ in range(3):
        with pytest.raises(LiveProviderExecutionBlockedError) as excinfo:
            execute_call_plan(plan, config, gate)
        messages.add(str(excinfo.value))
    assert len(messages) == 1


def test_injected_transport_is_rejected():
    config, gate, plan = _plan_for("openai_api")
    for transport in (object(), "https", lambda p: None, {}):
        with pytest.raises(LiveProviderExecutionBlockedError):
            execute_call_plan(plan, config, gate, transport=transport)


def test_injected_resolver_is_rejected():
    config, gate, plan = _plan_for("openai_api")
    for resolver in (OfflineCredentialResolver(), object(), lambda r: None):
        with pytest.raises(LiveProviderExecutionBlockedError):
            execute_call_plan(plan, config, gate, resolver=resolver)


@pytest.mark.parametrize("live", [True, 1, "yes", []])
def test_live_requests_are_rejected(live):
    config, gate, plan = _plan_for("openai_api")
    with pytest.raises(LiveProviderExecutionBlockedError):
        execute_call_plan(plan, config, gate, live=live)


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "anthropic_api"),
        ("action", "chat"),
        ("mode", "manual_operator"),
        ("gate_id", "0" * 64),
        ("config_sha256", "1" * 64),
    ],
)
def test_tampered_plan_fields_are_detected(field, value):
    config, gate, plan = _plan_for("openai_api")
    tampered = dict(plan)
    tampered[field] = value
    with pytest.raises(
        (CallPlanTamperError, CallPlanValidationError, CallPlanStaleError)
    ):
        execute_call_plan(tampered, config, gate)


def test_unknown_plan_version_is_stale():
    config, gate, plan = _plan_for("openai_api")
    tampered = dict(plan)
    tampered["plan_version"] = 99
    with pytest.raises((CallPlanStaleError, CallPlanTamperError)):
        execute_call_plan(tampered, config, gate)


def test_config_drift_makes_plan_stale():
    manifest = build_health_request(OPENAI_CONFIG)
    gate = issue_gate(
        OPENAI_CONFIG, run_id=RUN, action=ACTION, operator_ack=_ack_for("openai_api")
    )
    plan = build_call_plan(OPENAI_CONFIG, manifest, gate, run_id=RUN, action=ACTION)
    drifted = {"provider": "openai_api"}  # reference removed -> different hash
    with pytest.raises(CallPlanStaleError):
        execute_call_plan(plan, drifted, gate)


@pytest.mark.parametrize("bogus", [None, {}, "plan", 42, {"plan_id": "x"}])
def test_execution_without_a_plan_is_impossible(bogus):
    gate = issue_gate(
        OPENAI_CONFIG, run_id=RUN, action=ACTION, operator_ack=_ack_for("openai_api")
    )
    with pytest.raises(CallPlanValidationError):
        execute_call_plan(bogus, OPENAI_CONFIG, gate)


@pytest.mark.parametrize("bogus_gate", [None, {}, "gate", 42])
def test_execution_without_the_bound_gate_is_impossible(bogus_gate):
    config, _, plan = _plan_for("openai_api")
    with pytest.raises(GateValidationError):
        execute_call_plan(plan, config, bogus_gate)


def test_execution_never_mutates_plan_or_config():
    config, gate, plan = _plan_for("openai_api")
    config_snap = copy.deepcopy(config)
    gate_snap = copy.deepcopy(gate)
    plan_snap = copy.deepcopy(plan)
    with pytest.raises(LiveProviderExecutionBlockedError):
        execute_call_plan(plan, config, gate)
    assert config == config_snap
    assert gate == gate_snap
    assert plan == plan_snap


def test_forged_plan_request_fields_are_rejected_at_the_seam():
    # Hash-self-consistent forgery: session field inside the request.
    config, gate, plan = _plan_for("openai_api")
    forged = _reforge(
        plan, "plan_id", request={**plan["request"], "cookie": "opaque-value"}
    )
    with pytest.raises(CallPlanValidationError):
        execute_call_plan(forged, config, gate)


def test_forged_plan_request_target_is_rejected_at_the_seam():
    config, gate, plan = _plan_for("openai_api")
    forged = _reforge(
        plan, "plan_id", request={**plan["request"], "host": "exfil.example.com"}
    )
    with pytest.raises(CallPlanValidationError):
        execute_call_plan(forged, config, gate)


def test_forged_execution_flags_are_rejected_at_the_seam():
    config, gate, plan = _plan_for("openai_api")
    forged = _reforge(
        plan,
        "plan_id",
        execution={
            **plan["execution"],
            "operator_gate_required": False,
            "executable_in_this_build": True,
        },
    )
    with pytest.raises(CallPlanValidationError):
        execute_call_plan(forged, config, gate)


def test_forged_plan_identifiers_are_rejected_at_the_seam():
    config, gate, plan = _plan_for("openai_api")
    for field in ("gate_id", "manifest_id"):
        forged = _reforge(plan, "plan_id", **{field: "0" * 64})
        with pytest.raises(CallPlanValidationError):
            execute_call_plan(forged, config, gate)


def test_forged_plan_run_id_is_rejected_by_gate_binding():
    config, gate, plan = _plan_for("openai_api")
    forged = _reforge(plan, "plan_id", run_id="fedcba987654")
    with pytest.raises(GateValidationError):
        execute_call_plan(forged, config, gate)


def test_plan_for_one_gate_cannot_execute_with_another():
    config, _, plan = _plan_for("openai_api")
    other_gate = issue_gate(
        config,
        run_id=RUN,
        action=ACTION,
        operator_ack={
            "confirmed": True,
            "statement": "I authorize one different health_check on openai_api",
        },
    )
    with pytest.raises(CallPlanValidationError):
        execute_call_plan(plan, config, other_gate)


# --- structural guarantees ----------------------------------------------------


def test_all_new_errors_are_provider_config_errors():
    for exc in (CallPlanValidationError, CallPlanTamperError, CallPlanStaleError):
        assert issubclass(exc, ProviderConfigError)


def test_module_source_imports_no_io_machinery():
    source = PLAN_SOURCE.read_text(encoding="utf-8")
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
        "open(",
        "import os",
        "import pathlib",
    ):
        assert forbidden not in source, forbidden
