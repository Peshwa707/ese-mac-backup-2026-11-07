"""Offline HTTP contract tests for full-text access routing."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import create_server


ENGINE_ROOT = Path(__file__).resolve().parents[2] / "evidence-synthesis-engine-screen-prove"


def _server(tmp_path):
    server = create_server(
        host="127.0.0.1", port=0, engine_root=ENGINE_ROOT, data_root=tmp_path / "runs"
    )
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


def test_full_text_registry_and_plan_are_offline_and_deterministic(tmp_path):
    citation = {
        "doi": "10.1000/fixture",
        "pmid": "39551604",
        "title": "Fixture article",
    }
    server, thread = _server(tmp_path)
    try:
        registry_status, registry_body = _request(server, "/api/full-text/connectors")
        first = _request(
            server, "/api/full-text/plan", method="POST", payload={"citation": citation}
        )
        second = _request(
            server, "/api/full-text/plan", method="POST", payload={"citation": citation}
        )
    finally:
        server.shutdown()
        thread.join()

    assert registry_status == first[0] == second[0] == 200
    assert first[1] == second[1]
    registry = json.loads(registry_body)
    assert registry["access_version"] == 1
    assert "scopus" in {entry["connector_id"] for entry in registry["connectors"]}
    plan = json.loads(first[1])
    assert plan["identity"]["pmid"] == "39551604"
    assert plan["routes"][-1]["connector_id"] == "manual_browser"
    assert all(route["verified_full_text"] is False for route in plan["routes"])


def test_full_text_plan_accepts_only_explicit_connector_subset(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/full-text/plan",
            method="POST",
            payload={
                "citation": {"title": "Fixture"},
                "enabled_connectors": ["ezproxy", "publisher"],
            },
        )
    finally:
        server.shutdown()
        thread.join()
    assert status == 200
    assert [route["connector_id"] for route in json.loads(body)["routes"]] == [
        "ezproxy",
        "publisher",
    ]


def test_full_text_access_profile_endpoint_is_reference_only(tmp_path):
    profile = {
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
    server, thread = _server(tmp_path)
    try:
        registry_status, registry_body = _request(
            server, "/api/full-text/access-config"
        )
        validate_status, validate_body = _request(
            server,
            "/api/full-text/access-config/validate",
            method="POST",
            payload=profile,
        )
    finally:
        server.shutdown()
        thread.join()
    assert registry_status == validate_status == 200
    registry = json.loads(registry_body)
    assert registry["access_config_version"] == 1
    assert "api_key_reference" in next(
        item for item in registry["connectors"] if item["connector_id"] == "scopus"
    )["modes"]
    result = json.loads(validate_body)
    assert result["connectors"][0]["credential_ref"] == {
        "kind": "env",
        "name": "SCOPUS_API_KEY",
    }
    assert "SCOPUS_SECRET_VALUE" not in validate_body.decode()


def test_full_text_plan_can_use_validated_access_profile(tmp_path):
    server, thread = _server(tmp_path)
    try:
        config_status, config_body = _request(
            server,
            "/api/full-text/access-config/validate",
            method="POST",
            payload={
                "connectors": [
                    {
                        "connector_id": "scopus",
                        "enabled": True,
                        "mode": "api_key_reference",
                        "credential_ref": {"kind": "env", "name": "SCOPUS_API_KEY"},
                    }
                ]
            },
        )
        plan_status, plan_body = _request(
            server,
            "/api/full-text/plan",
            method="POST",
            payload={
                "citation": {"doi": "10.1000/fixture"},
                "access_config": json.loads(config_body),
            },
        )
    finally:
        server.shutdown()
        thread.join()
    assert config_status == plan_status == 200
    routes = {route["connector_id"]: route for route in json.loads(plan_body)["routes"]}
    assert routes["scopus"]["configuration_state"] == "configured_reference_only"
    assert json.loads(plan_body)["access_config_id"]


def test_full_text_probe_plan_is_offline_and_never_executes(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/full-text/probe-plan",
            method="POST",
            payload={
                "citation": {"doi": "10.1000/fixture"},
                "access_config": {
                    "connectors": [
                        {
                            "connector_id": "scopus",
                            "enabled": True,
                            "mode": "api_key_reference",
                            "credential_ref": {"kind": "env", "name": "SCOPUS_API_KEY"},
                        }
                    ]
                },
            },
        )
    finally:
        server.shutdown()
        thread.join()
    assert status == 200
    plan = json.loads(body)
    assert plan["operation"] == "metadata_or_entitlement_probe"
    assert plan["request"]["host"] == "api.elsevier.com"
    assert plan["full_text_verified"] is False
    assert "headers" not in plan["request"]


def test_manual_access_record_endpoint_verifies_identity_without_writing_run_artifacts(tmp_path):
    data_root = tmp_path / "runs"
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/full-text/manual-record",
            method="POST",
            payload={
                "citation": {
                    "doi": "10.1000/ABC.1",
                    "pmid": "39551604",
                    "title": "A bounded fixture article",
                },
                "route_id": "google_scholar",
                "access_state": "full_text_observed",
                "candidate": {
                    "doi": "10.1000/abc.1",
                    "pmid": "39551604",
                    "title": "A bounded fixture article",
                },
                "source_url": "https://scholar.example/article",
                "operator_note": "Observed manually.",
            },
        )
    finally:
        server.shutdown()
        thread.join()
    assert status == 200
    record = json.loads(body)
    assert record["verified_full_text"] is True
    assert record["identity_check"]["match"] is True
    assert not data_root.exists() or not list(data_root.rglob("*"))


def test_full_text_endpoints_reject_secret_and_do_not_create_run_artifacts(tmp_path):
    data_root = tmp_path / "runs"
    server, thread = _server(tmp_path)
    try:
        status, body = _request(
            server,
            "/api/full-text/plan",
            method="POST",
            payload={"citation": {"title": "sk-ant-secret"}},
        )
    finally:
        server.shutdown()
        thread.join()
    assert status == 400
    assert b"sk-ant-secret" not in body
    assert not data_root.exists() or not list(data_root.rglob("*"))


def test_static_ui_mentions_full_text_routing_and_manual_login_boundary(tmp_path):
    server, thread = _server(tmp_path)
    try:
        html_status, html = _request(server, "/")
        js_status, js = _request(server, "/static/app.js")
    finally:
        server.shutdown()
        thread.join()
    assert html_status == js_status == 200
    assert b"full-text" in html
    assert b"EZproxy" in html
    assert b"/api/full-text/plan" in js
    assert b"reference-only institutional access profile" in html
    assert b"/api/full-text/access-config/validate" in js
    assert b"/api/full-text/probe-plan" in js
    assert b"/api/full-text/manual-record" in js
    assert b"Record a manual access result" in html
