"""Smoke tests for the Phase 6 web app shell (web/server.py).

Verifies the static frontend is served at / and the engine API is mounted
under /api — without exercising the LLM/network pipeline.
"""

from fastapi.testclient import TestClient

from web.server import app

client = TestClient(app)


def test_index_served_at_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Onco" in r.text  # the page title/brand renders


def test_static_assets_served():
    for path in ("/styles.css", "/app.js"):
        r = client.get(path)
        assert r.status_code == 200, path


def test_api_mounted_under_api_prefix():
    # Unknown report id -> the engine API's 404 JSON, proving /api is mounted.
    r = client.get("/api/report/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"]


def test_api_analyze_validation_via_prefix():
    # Missing required field -> 422 from the mounted API (no pipeline run).
    r = client.post("/api/analyze", json={})
    assert r.status_code == 422
