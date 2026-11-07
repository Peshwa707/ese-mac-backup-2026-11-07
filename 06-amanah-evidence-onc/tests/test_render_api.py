"""Offline checks for rendering and the API contract (no network/LLM)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from core.api import app as api_app
from core.config.vertical import load_vertical
from core.grounding.deid import PHIDetectedError
from core.models import (
    Citation,
    EvidenceGrade,
    Finding,
    IdType,
    Report,
    SourceStatus,
)
from core.render import render


def _sample_report() -> Report:
    cite = Citation(
        id_type=IdType.PMID,
        id="111",
        source="pubmed",
        title="A randomized trial",
        url="https://pubmed.ncbi.nlm.nih.gov/111/",
        snippet="Treatment improved survival.",
        verified=True,
    )
    finding = Finding(
        statement="Option A is supported.",
        citations=[cite],
        quote="improved survival",
        evidence_grade=EvidenceGrade.RCT,
        confidence=0.8,
    )
    return Report(
        id="abc123",
        vertical="oncology",
        structured_case={"diagnosis": "example"},
        decision_support=[finding],
        sources_status=[SourceStatus(source="pubmed", available=True)],
        provenance_id="abc123",
        disclaimer="Decision support only.",
    )


def test_render_html_and_json():
    report = _sample_report()
    vertical = load_vertical("oncology")
    html = render.render_html(report, vertical)
    assert "Option A is supported." in html
    assert "[pubmed:111]" in html
    assert "Decision support only." in html
    js = render.render_json(report)
    assert '"id": "abc123"' in js


def test_render_manuscript_template():
    report = _sample_report()
    report.manuscript = {
        "introduction": "Intro text",
        "case_presentation": "Case text",
        "discussion": "Discussion text",
        "literature_review": "Lit review text",
    }
    html = render.render_html(report, load_vertical("oncology"), manuscript=True)
    assert "Intro text" in html and "References" in html


def test_api_unknown_report_404():
    client = TestClient(api_app.app)
    assert client.get("/report/does-not-exist").status_code == 404


def test_api_export_paths_for_completed_job():
    client = TestClient(api_app.app)
    # Inject a completed job directly into the in-memory store.
    api_app._JOBS["job1"] = {"status": "done", "report": _sample_report()}
    try:
        assert client.get("/report/job1").json()["status"] == "done"
        assert client.get("/report/job1/export?format=json").status_code == 200
        html_resp = client.get("/report/job1/export?format=html")
        assert html_resp.status_code == 200 and "Option A" in html_resp.text
    finally:
        api_app._JOBS.pop("job1", None)


# ---------------------------------------------------------------------------
# NEW TESTS
# ---------------------------------------------------------------------------


def test_post_analyze_returns_job_id():
    """POST /analyze with run_case patched → returns a job_id immediately."""
    client = TestClient(api_app.app, raise_server_exceptions=False)
    with patch("core.api.app.run_case", new=AsyncMock(return_value=_sample_report())):
        resp = client.post("/analyze", json={"case_text": "patient with NSCLC"})
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert isinstance(data["job_id"], str) and len(data["job_id"]) > 0
    # Clean up
    api_app._JOBS.pop(data["job_id"], None)


def test_phi_rejected_path():
    """run_case raising PHIDetectedError → job status becomes 'rejected'."""
    client = TestClient(api_app.app, raise_server_exceptions=False)
    phi_err = PHIDetectedError("Residual PHI of type NAME survived de-identification")
    with patch("core.api.app.run_case", new=AsyncMock(side_effect=phi_err)):
        resp = client.post("/analyze", json={"case_text": "Mr. John Smith has NSCLC"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Give the background task a moment to complete (TestClient runs sync event loop)
    job = api_app._JOBS.get(job_id, {})
    assert job.get("status") == "rejected"
    error_msg = job.get("error", "")
    assert "PHI" in error_msg
    # Confirm the error is surfaced via GET /report/{id}
    report_resp = client.get(f"/report/{job_id}")
    assert report_resp.json()["status"] == "rejected"
    api_app._JOBS.pop(job_id, None)


def test_export_running_job_returns_409():
    """GET /report/{id}/export when job is still running → 409 Conflict."""
    client = TestClient(api_app.app)
    api_app._JOBS["running_job"] = {"status": "running"}
    try:
        resp = client.get("/report/running_job/export?format=json")
        assert resp.status_code == 409
    finally:
        api_app._JOBS.pop("running_job", None)


def test_provenance_missing_returns_404():
    """GET /report/{id}/provenance when no provenance was recorded → 404."""
    client = TestClient(api_app.app)
    api_app._JOBS["noprov_job"] = {"status": "done", "report": _sample_report()}
    try:
        with patch("core.api.app.provenance_store.read", return_value=None):
            resp = client.get("/report/noprov_job/provenance")
        assert resp.status_code == 404
    finally:
        api_app._JOBS.pop("noprov_job", None)


def test_export_pdf_returns_503_when_render_raises():
    """GET /report/{id}/export?format=pdf when render_pdf raises RuntimeError → 503."""
    client = TestClient(api_app.app)
    api_app._JOBS["pdf_job"] = {"status": "done", "report": _sample_report()}
    try:
        with patch("core.api.app.render.render_pdf", side_effect=RuntimeError("weasyprint missing")):
            resp = client.get("/report/pdf_job/export?format=pdf")
        assert resp.status_code == 503
    finally:
        api_app._JOBS.pop("pdf_job", None)


def test_export_invalid_format_returns_422():
    """GET /report/{id}/export?format=invalid → 422 Unprocessable Entity."""
    client = TestClient(api_app.app)
    api_app._JOBS["fmt_job"] = {"status": "done", "report": _sample_report()}
    try:
        resp = client.get("/report/fmt_job/export?format=invalid")
        assert resp.status_code == 422
    finally:
        api_app._JOBS.pop("fmt_job", None)


# ---------------------------------------------------------------------------
# Phase-5 hardening: Auth tests
# ---------------------------------------------------------------------------


def test_auth_token_set_no_header_returns_401(monkeypatch):
    """When ONCO_API_TOKEN is set and no Authorization header → 401."""
    monkeypatch.setenv("ONCO_API_TOKEN", "secret-token")
    # Reset the one-time warning sentinel so auth.py re-evaluates cleanly.
    import core.api.auth as auth_mod
    auth_mod._warned_no_auth = False

    client = TestClient(api_app.app, raise_server_exceptions=True)
    api_app._JOBS["auth_job1"] = {"status": "done", "report": _sample_report(), "_created_at": __import__("time").monotonic()}
    try:
        resp = client.get("/report/auth_job1")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"
    finally:
        api_app._JOBS.pop("auth_job1", None)


def test_auth_token_set_correct_header_returns_200(monkeypatch):
    """When ONCO_API_TOKEN is set and correct bearer token is supplied → 200."""
    monkeypatch.setenv("ONCO_API_TOKEN", "secret-token")
    import core.api.auth as auth_mod
    auth_mod._warned_no_auth = False

    client = TestClient(api_app.app, raise_server_exceptions=True)
    api_app._JOBS["auth_job2"] = {"status": "done", "report": _sample_report(), "_created_at": __import__("time").monotonic()}
    try:
        resp = client.get("/report/auth_job2", headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
    finally:
        api_app._JOBS.pop("auth_job2", None)


def test_auth_token_set_wrong_token_returns_401(monkeypatch):
    """When ONCO_API_TOKEN is set and wrong bearer token is supplied → 401."""
    monkeypatch.setenv("ONCO_API_TOKEN", "secret-token")
    import core.api.auth as auth_mod
    auth_mod._warned_no_auth = False

    client = TestClient(api_app.app, raise_server_exceptions=True)
    api_app._JOBS["auth_job3"] = {"status": "done", "report": _sample_report(), "_created_at": __import__("time").monotonic()}
    try:
        resp = client.get("/report/auth_job3", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"
    finally:
        api_app._JOBS.pop("auth_job3", None)


def test_auth_token_unset_requests_succeed(monkeypatch):
    """When ONCO_API_TOKEN is unset (dev mode) requests succeed without a header."""
    monkeypatch.delenv("ONCO_API_TOKEN", raising=False)
    import core.api.auth as auth_mod
    auth_mod._warned_no_auth = False

    client = TestClient(api_app.app, raise_server_exceptions=True)
    api_app._JOBS["auth_job4"] = {"status": "done", "report": _sample_report(), "_created_at": __import__("time").monotonic()}
    try:
        resp = client.get("/report/auth_job4")
        assert resp.status_code == 200
    finally:
        api_app._JOBS.pop("auth_job4", None)


# ---------------------------------------------------------------------------
# Phase-5 hardening: TTL eviction tests
# ---------------------------------------------------------------------------


def test_ttl_expired_job_returns_404(monkeypatch):
    """A job whose _created_at is older than the TTL is pruned → GET returns 404."""
    import time

    monkeypatch.delenv("ONCO_API_TOKEN", raising=False)
    monkeypatch.setenv("ONCO_JOB_TTL_SECONDS", "10")

    client = TestClient(api_app.app, raise_server_exceptions=True)
    # Insert a job with a timestamp 100 seconds in the past (well beyond the 10-s TTL).
    api_app._JOBS["ttl_job"] = {
        "status": "done",
        "report": _sample_report(),
        "_created_at": time.monotonic() - 100,
    }
    try:
        resp = client.get("/report/ttl_job")
        assert resp.status_code == 404
    finally:
        api_app._JOBS.pop("ttl_job", None)


def test_ttl_live_job_not_pruned(monkeypatch):
    """A job within the TTL window is NOT pruned → GET returns 200."""
    import time

    monkeypatch.delenv("ONCO_API_TOKEN", raising=False)
    monkeypatch.setenv("ONCO_JOB_TTL_SECONDS", "3600")

    client = TestClient(api_app.app, raise_server_exceptions=True)
    api_app._JOBS["ttl_live_job"] = {
        "status": "done",
        "report": _sample_report(),
        "_created_at": time.monotonic(),
    }
    try:
        resp = client.get("/report/ttl_live_job")
        assert resp.status_code == 200
    finally:
        api_app._JOBS.pop("ttl_live_job", None)
