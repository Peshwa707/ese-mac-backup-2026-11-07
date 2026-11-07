"""FastAPI boundary — the stable JSON contract any future app consumes.

Endpoints (spec §6):
  POST /analyze                          -> {job_id}
  GET  /report/{id}                      -> status + Report
  GET  /report/{id}/export?format=...    -> pdf | html | json
  GET  /report/{id}/provenance           -> audit trail

Auth: bearer-token via ``Authorization: Bearer <token>`` when
``EVIDENCE_API_TOKEN`` (alias: ``ONCO_API_TOKEN``) env var is set; dev mode (no
token required) when unset.

Job store: in-memory with TTL eviction.  Each entry carries a monotonic
creation timestamp; entries older than ``EVIDENCE_JOB_TTL_SECONDS`` (alias:
``ONCO_JOB_TTL_SECONDS``; default 3600 s) are pruned on every access and insert.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from core.api.auth import auth_ctx
from core.config.vertical import load_vertical
from core.grounding.deid import PHIDetectedError
from core.models import Report
from core.pipeline import run_case
from core.render import provenance as provenance_store
from core.render import render

app = FastAPI(title="Evidence Engine", version="0.1.0")

# ---------------------------------------------------------------------------
# In-memory job store with TTL eviction.
# Each value is a dict that always contains:
#   "_created_at": float  — time.monotonic() stamp set on insert
#   "status": str
#   (optionally) "report", "error"
# ---------------------------------------------------------------------------
_JOBS: dict[str, dict[str, Any]] = {}

# Strong references to in-flight background tasks. asyncio only holds a WEAK
# reference to a bare-created task, so without this the job coroutine can be
# garbage-collected mid-run, leaving the job stuck at status "running" forever.
_BG_TASKS: set[asyncio.Task[Any]] = set()

_DEFAULT_JOB_TTL = 3600  # seconds


def _job_ttl() -> float:
    """Return configured TTL in seconds, read from env at call time."""
    from core.paths import env
    try:
        return float(env("EVIDENCE_JOB_TTL_SECONDS", "ONCO_JOB_TTL_SECONDS",
                          default=str(_DEFAULT_JOB_TTL)))
    except (TypeError, ValueError):
        return float(_DEFAULT_JOB_TTL)


def _prune_jobs() -> None:
    """Remove jobs whose age exceeds the configured TTL."""
    ttl = _job_ttl()
    now = time.monotonic()
    expired = [jid for jid, job in _JOBS.items() if now - job.get("_created_at", now) > ttl]
    for jid in expired:
        _JOBS.pop(jid, None)


def _insert_job(job_id: str, data: dict[str, Any]) -> None:
    """Insert or update a job, stamping creation time and pruning stale entries."""
    _prune_jobs()
    data.setdefault("_created_at", time.monotonic())
    _JOBS[job_id] = data


class AnalyzeRequest(BaseModel):
    case_text: str
    vertical: Optional[str] = None
    make_manuscript: bool = False


class AnalyzeResponse(BaseModel):
    job_id: str


async def _run_job(job_id: str, req: AnalyzeRequest) -> None:
    try:
        report = await run_case(
            req.case_text,
            vertical_name=req.vertical,
            make_manuscript=req.make_manuscript,
            report_id=job_id,
        )
        # Preserve the original creation timestamp when updating status.
        created_at = _JOBS.get(job_id, {}).get("_created_at", time.monotonic())
        _JOBS[job_id] = {"status": "done", "report": report, "_created_at": created_at}
    except PHIDetectedError as exc:
        created_at = _JOBS.get(job_id, {}).get("_created_at", time.monotonic())
        _JOBS[job_id] = {
            "status": "rejected",
            "error": f"PHI detected: {exc}",
            "_created_at": created_at,
        }
    except Exception:  # surface failures rather than hang; no str(exc) leak
        logger.exception("Unhandled error processing job %s", job_id)
        created_at = _JOBS.get(job_id, {}).get("_created_at", time.monotonic())
        _JOBS[job_id] = {
            "status": "error",
            "error": "internal processing error",
            "_created_at": created_at,
        }


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, _ctx: dict = Depends(auth_ctx)) -> AnalyzeResponse:
    import uuid

    job_id = uuid.uuid4().hex
    _insert_job(job_id, {"status": "running"})
    # Hold a strong reference until the task finishes (see _BG_TASKS) so the
    # event loop cannot garbage-collect the running job.
    task = asyncio.ensure_future(_run_job(job_id, req))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return AnalyzeResponse(job_id=job_id)


def _job_or_404(job_id: str) -> dict[str, Any]:
    _prune_jobs()
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job/report id")
    return job


@app.get("/report/{job_id}")
async def get_report(job_id: str, _ctx: dict = Depends(auth_ctx)) -> JSONResponse:
    job = _job_or_404(job_id)
    payload: dict[str, Any] = {"status": job["status"]}
    if "error" in job:
        payload["error"] = job["error"]
    if "report" in job:
        payload["report"] = job["report"].model_dump(mode="json")
    return JSONResponse(payload)


def _ready_report(job_id: str) -> Report:
    job = _job_or_404(job_id)
    report = job.get("report")
    if report is None:
        raise HTTPException(status_code=409, detail=f"report not ready (status={job['status']})")
    return report


@app.get("/report/{job_id}/export")
async def export_report(
    job_id: str,
    format: str = Query("json", pattern="^(pdf|html|json)$"),
    manuscript: bool = False,
    _ctx: dict = Depends(auth_ctx),
):
    report = _ready_report(job_id)
    vertical = load_vertical(report.vertical)
    if format == "json":
        return PlainTextResponse(render.render_json(report), media_type="application/json")
    if format == "html":
        return HTMLResponse(render.render_html(report, vertical, manuscript=manuscript))
    # pdf
    try:
        pdf = render.render_pdf(report, vertical, manuscript=manuscript)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(pdf, media_type="application/pdf")


@app.get("/report/{job_id}/provenance")
async def get_provenance(job_id: str, _ctx: dict = Depends(auth_ctx)) -> JSONResponse:
    _job_or_404(job_id)
    prov = provenance_store.read(job_id)
    if prov is None:
        raise HTTPException(status_code=404, detail="no provenance recorded")
    return JSONResponse(prov.model_dump(mode="json"))
