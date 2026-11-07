"""Offline HTTP contract tests for the provider control surface."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from server import create_server


ENGINE_ROOT = Path("/Users/neurologic/Projects/evidence-synthesis-engine-screen-prove")


def _server(tmp_path):
    server = create_server(host="127.0.0.1", port=0, engine_root=ENGINE_ROOT, data_root=tmp_path / "runs")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(server, path: str, *, method: str = "GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


API_CONFIGS = [
    {"provider": "openai_api", "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"}},
    {"provider": "anthropic_api", "credential_ref": {"kind": "env", "name": "ANTHROPIC_API_KEY"}},
    {"provider": "ollama_local"},
    {"provider": "ollama_cloud", "credential_ref": {"kind": "env", "name": "OLLAMA_API_KEY"}},
    {"provider": "moonshot_kimi_api", "credential_ref": {"kind": "env", "name": "MOONSHOT_API_KEY"}},
    {"provider": "deepseek_api", "credential_ref": {"kind": "env", "name": "DEEPSEEK_API_KEY"}},
]


def test_provider_registry_lists_all_modes_deterministically(tmp_path):
    server, thread = _server(tmp_path)
    try:
        first_status, first = _request(server, "/api/providers")
        second_status, second = _request(server, "/api/providers")
    finally:
        server.shutdown()
        thread.join()

    assert first_status == second_status == 200
    assert first == second
    payload = json.loads(first)
    assert set(payload) == {"checks", "registry"}
    assert set(payload["registry"]) == {
        "openai_api",
        "anthropic_api",
        "ollama_local",
        "ollama_cloud",
        "moonshot_kimi_api",
        "deepseek_api",
        "interactive_subscription",
    }


@pytest.mark.parametrize("config", API_CONFIGS + [{"provider": "interactive_subscription", "service": "chatgpt"}])
def test_validate_accepts_reference_only_configuration_for_each_mode(tmp_path, config):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(server, "/api/providers/validate", method="POST", payload=config)
    finally:
        server.shutdown()
        thread.join()

    assert status == 200
    result = json.loads(body)
    assert result["provider"] == config["provider"]
    assert result["live_enabled"] is False
    assert "value" not in json.dumps(result)


def test_health_plan_and_manifest_are_offline_and_deterministic(tmp_path):
    config = API_CONFIGS[0]
    server, thread = _server(tmp_path)
    try:
        health_first = _request(server, "/api/providers/health-plan", method="POST", payload=config)
        health_second = _request(server, "/api/providers/health-plan", method="POST", payload=config)
        manifest_status, manifest_body = _request(server, "/api/providers/manifest", method="POST", payload=config)
    finally:
        server.shutdown()
        thread.join()

    assert health_first[0] == health_second[0] == manifest_status == 200
    assert health_first[1] == health_second[1]
    health = json.loads(health_first[1])
    assert health["live"] is False
    assert health["steps"][-1]["status"] == "blocked_offline"
    manifest = json.loads(manifest_body)
    assert manifest["http"]["auth"] == {"header": "Authorization", "scheme": "bearer"}
    assert "value" not in manifest["http"]["auth"]


def test_subscription_manifest_is_manual_and_has_no_http_section(tmp_path):
    config = {"provider": "interactive_subscription", "service": "claude"}
    server, thread = _server(tmp_path)
    try:
        status, body = _request(server, "/api/providers/manifest", method="POST", payload=config)
    finally:
        server.shutdown()
        thread.join()

    assert status == 200
    manifest = json.loads(body)
    assert "http" not in manifest
    assert manifest["requires"] == {"manual_operator": True, "network": False}
    assert "automates" in manifest["manual_instruction"]


def test_secret_and_live_requests_fail_without_echoing_secret(tmp_path):
    secret = "sk-live-DO_NOT_ECHO_THIS"
    server, thread = _server(tmp_path)
    try:
        secret_status, secret_body = _request(
            server,
            "/api/providers/validate",
            method="POST",
            payload={"provider": "openai_api", "credential_ref": {"kind": "env", "name": secret}},
        )
        live_status, live_body = _request(
            server,
            "/api/providers/validate",
            method="POST",
            payload={"provider": "openai_api", "live_enabled": True},
        )
    finally:
        server.shutdown()
        thread.join()

    assert secret_status == live_status == 400
    assert secret.encode() not in secret_body
    assert b"live_enabled" not in live_body


def test_provider_endpoints_do_not_create_run_artifacts(tmp_path):
    data_root = tmp_path / "runs"
    server, thread = _server(tmp_path)
    before = sorted(path.relative_to(data_root).as_posix() for path in data_root.rglob("*"))
    try:
        for path, method, payload in (
            ("/api/providers", "GET", None),
            ("/api/providers/validate", "POST", API_CONFIGS[2]),
            ("/api/providers/health-plan", "POST", API_CONFIGS[2]),
            ("/api/providers/manifest", "POST", API_CONFIGS[2]),
        ):
            assert _request(server, path, method=method, payload=payload)[0] == 200
    finally:
        server.shutdown()
        thread.join()

    after = sorted(path.relative_to(data_root).as_posix() for path in data_root.rglob("*"))
    assert after == before


def test_static_provider_panel_keeps_subscriptions_manual(tmp_path):
    server, thread = _server(tmp_path)
    try:
        html_status, html = _request(server, "/")
        js_status, js = _request(server, "/static/app.js")
    finally:
        server.shutdown()
        thread.join()

    assert html_status == js_status == 200
    assert b'id="providers-panel"' in html
    assert b"interactive_subscription" in html
    assert b"Manual subscription session" in html
    assert b"/api/providers/${action}" in js
    assert b'runProviderAction("manifest")' in js
    assert b'runProviderAction("validate")' in js


def _gate_request(config):
    return {
        "config": config,
        "run_id": "abc123def456",
        "action": "health_check",
        "operator_ack": {
            "confirmed": True,
            "statement": (
                "I authorize one health_check for provider "
                f"{config['provider']} in this offline control surface."
            ),
        },
    }


def test_gate_endpoint_returns_deterministic_single_use_artifact_without_execution(tmp_path):
    server, thread = _server(tmp_path)
    request_body = _gate_request(API_CONFIGS[0])
    try:
        first_status, first_body = _request(
            server, "/api/providers/gate", method="POST", payload=request_body
        )
        second_status, second_body = _request(
            server, "/api/providers/gate", method="POST", payload=request_body
        )
    finally:
        server.shutdown()
        thread.join()

    assert first_status == second_status == 200
    assert first_body == second_body
    gate = json.loads(first_body)
    assert gate["provider"] == "openai_api"
    assert gate["run_id"] == "abc123def456"
    assert gate["action"] == "health_check"
    assert gate["single_use"] is True
    assert "sk-live" not in first_body.decode()


def test_call_plan_endpoint_requires_gate_and_remains_non_executable(tmp_path):
    config = API_CONFIGS[0]
    server, thread = _server(tmp_path)
    try:
        gate_status, gate_body = _request(
            server,
            "/api/providers/gate",
            method="POST",
            payload=_gate_request(config),
        )
        assert gate_status == 200
        gate = json.loads(gate_body)
        payload = {
            "config": config,
            "gate": gate,
            "run_id": "abc123def456",
            "action": "health_check",
        }
        first_status, first_body = _request(
            server, "/api/providers/call-plan", method="POST", payload=payload
        )
        second_status, second_body = _request(
            server, "/api/providers/call-plan", method="POST", payload=payload
        )
    finally:
        server.shutdown()
        thread.join()

    assert first_status == second_status == 200
    assert first_body == second_body
    plan = json.loads(first_body)
    assert plan["provider"] == "openai_api"
    assert plan["execution"]["executable_in_this_build"] is False
    assert plan["execution"]["operator_gate_required"] is True
    assert "credential_value" not in first_body.decode()


def test_call_plan_endpoint_rejects_mismatched_tampered_and_missing_gates(tmp_path):
    config = API_CONFIGS[0]
    foreign_config = API_CONFIGS[-1]
    server, thread = _server(tmp_path)
    try:
        gate_status, gate_body = _request(
            server,
            "/api/providers/gate",
            method="POST",
            payload=_gate_request(config),
        )
        assert gate_status == 200
        gate = json.loads(gate_body)
        tampered_gate = {**gate, "provider": "deepseek_api"}
        payloads = [
            {"config": foreign_config, "gate": gate},
            {"config": config, "gate": tampered_gate},
            {"config": config},
        ]
        responses = [
            _request(
                server,
                "/api/providers/call-plan",
                method="POST",
                payload={
                    **payload,
                    "run_id": "abc123def456",
                    "action": "health_check",
                },
            )
            for payload in payloads
        ]
    finally:
        server.shutdown()
        thread.join()

    assert [status for status, _ in responses] == [400, 400, 400]
    assert all(b"sk-live" not in body for _, body in responses)


def test_call_plan_endpoint_rejects_secret_shaped_config_without_echo(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/providers/call-plan",
            method="POST",
            payload={
                "config": {
                    "provider": "openai_api",
                    "credential_ref": {
                        "kind": "env",
                        "name": "sk-live-DO_NOT_ECHO_THIS",
                    },
                },
                "run_id": "abc123def456",
                "action": "health_check",
            },
        )
    finally:
        server.shutdown()
        thread.join()

    assert status == 400
    assert b"sk-live-DO_NOT_ECHO_THIS" not in body


def test_provider_control_routes_reject_unknown_fields_without_echo(tmp_path):
    config = API_CONFIGS[0]
    server, thread = _server(tmp_path)
    try:
        gate_status, gate_body = _request(
            server,
            "/api/providers/gate",
            method="POST",
            payload={**_gate_request(config), "secret": "sk-live-DO_NOT_ECHO_THIS"},
        )
        assert gate_status == 400
        plan_status, plan_body = _request(
            server,
            "/api/providers/call-plan",
            method="POST",
            payload={
                "config": config,
                "gate": {},
                "run_id": "abc123def456",
                "action": "health_check",
                "secret": "sk-live-DO_NOT_ECHO_THIS",
            },
        )
    finally:
        server.shutdown()
        thread.join()

    assert plan_status == 400
    assert b"sk-live-DO_NOT_ECHO_THIS" not in gate_body
    assert b"sk-live-DO_NOT_ECHO_THIS" not in plan_body


def test_subscription_call_plan_has_no_automated_request(tmp_path):
    config = {"provider": "interactive_subscription", "service": "claude"}
    server, thread = _server(tmp_path)
    try:
        gate_status, gate_body = _request(
            server,
            "/api/providers/gate",
            method="POST",
            payload=_gate_request(config),
        )
        assert gate_status == 200
        status, body = _request(
            server,
            "/api/providers/call-plan",
            method="POST",
            payload={
                "config": config,
                "gate": json.loads(gate_body),
                "run_id": "abc123def456",
                "action": "health_check",
            },
        )
    finally:
        server.shutdown()
        thread.join()

    assert status == 200
    plan = json.loads(body)
    assert "request" not in plan
    assert plan["execution"]["executor"] == "human_operator"
    assert plan["manual_instruction"]


def test_gate_endpoint_rejects_unconfirmed_or_incomplete_authorization(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/providers/gate",
            method="POST",
            payload={
                **_gate_request(API_CONFIGS[0]),
                "operator_ack": {"confirmed": False, "statement": "yes"},
            },
        )
    finally:
        server.shutdown()
        thread.join()

    assert status == 400
    assert b"confirmed" in body
    assert b"sk-live" not in body


def test_gate_endpoint_rejects_unapproved_action(tmp_path):
    config = API_CONFIGS[0]
    request_body = _gate_request(config)
    request_body["action"] = "live_call"
    request_body["operator_ack"]["statement"] = (
        f"I authorize live_call for provider {config['provider']}"
    )
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server, "/api/providers/gate", method="POST", payload=request_body
        )
    finally:
        server.shutdown()
        thread.join()

    assert status == 400
    assert b"health_check" in body


def test_provider_gate_rejects_non_object_json_body(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/providers/gate",
            method="POST",
            payload=["config", "run_id", "action", "operator_ack"],
        )
    finally:
        server.shutdown()
        thread.join()

    assert status == 400
    assert b"JSON object" in body


def test_gate_and_call_plan_do_not_create_run_artifacts(tmp_path):
    data_root = tmp_path / "runs"
    server, thread = _server(tmp_path)
    request_body = _gate_request(API_CONFIGS[0])
    before = sorted(path.relative_to(data_root).as_posix() for path in data_root.rglob("*"))
    try:
        gate_status, gate_body = _request(
            server, "/api/providers/gate", method="POST", payload=request_body
        )
        assert gate_status == 200
        plan_status, _ = _request(
            server,
            "/api/providers/call-plan",
            method="POST",
            payload={
                "config": API_CONFIGS[0],
                "gate": json.loads(gate_body),
                "run_id": "abc123def456",
                "action": "health_check",
            },
        )
        assert plan_status == 200
    finally:
        server.shutdown()
        thread.join()

    after = sorted(path.relative_to(data_root).as_posix() for path in data_root.rglob("*"))
    assert after == before


def test_static_provider_panel_exposes_gate_review_without_execution(tmp_path):
    server, thread = _server(tmp_path)
    try:
        html_status, html = _request(server, "/")
        js_status, js = _request(server, "/static/app.js")
    finally:
        server.shutdown()
        thread.join()

    assert html_status == js_status == 200
    assert b"Issue operator gate" in html
    assert b"Build gate-bound plan" in html
    assert b"does not send a live request" in html
    assert b"Live execution remains disabled" in html
    assert b"provider-health-v1" in html
    assert b'id="provider-plan-result"' in html
    assert b"/api/providers/gate" in js
    assert b"/api/providers/call-plan" in js
    assert b"clearProviderGate" in js
