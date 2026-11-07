"""Regression: background analyze task is strongly referenced (not GC-able)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from core.api import app as api_app


def _report():
    from core.models import Report
    return Report(id="x", vertical="oncology", disclaimer="d")


def test_analyze_tracks_task_then_cleans_up(monkeypatch):
    monkeypatch.delenv("ONCO_API_TOKEN", raising=False)
    import core.api.auth as auth_mod
    auth_mod._warned_no_auth = False

    client = TestClient(api_app.app, raise_server_exceptions=False)
    with patch("core.api.app.run_case", new=AsyncMock(return_value=_report())):
        resp = client.post("/analyze", json={"case_text": "patient with NSCLC"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    # The background task ran to completion (job done) and its done-callback
    # discarded it from the strong-reference set — no leak, no orphaned task.
    assert api_app._JOBS[job_id]["status"] == "done"
    assert all(t.done() for t in api_app._BG_TASKS)
    api_app._JOBS.pop(job_id, None)
