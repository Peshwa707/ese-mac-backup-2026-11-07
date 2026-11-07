"""Standalone web app for the Onco-Evidence Engine (Phase 6).

A thin product shell over the existing engine API. The generic engine API
(`core.api.app`) is mounted under ``/api``; the static vanilla-JS frontend is
served at ``/``. No engine logic lives here — this is purely the app layer
(spec §6: the JSON contract is the seam; any app consumes it).

Run:
    uvicorn web.server:app --reload --port 8000
then open http://localhost:8000

Auth: if ``ONCO_API_TOKEN`` is set, the frontend must supply it (a settings
field stores it in localStorage and sends ``Authorization: Bearer``). Unset =
dev mode (open).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.api.app import app as api_app
from web.meta_analysis import figure_path, load_meta_analysis

app = FastAPI(title="Onco-Evidence Web", version="0.1.0")

# The engine's stable JSON contract, namespaced under /api.
app.mount("/api", api_app)

# Product-shell UI-support endpoint (not part of the generic engine API). Serves the
# screening calibration so the dashboard can render the ROC / operating point. Computed
# live from the bundled labeled fixture — the only gold-labeled data available before a
# real review is screened; once an MM-BCMA run exists its calibration plugs in here.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CALIB_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "benchmark" / "real_run_min"


@app.get("/ui/calibration")
def ui_calibration():
    """CalibrationResult JSON for the calibration dashboard. Read-only; no user input."""
    from benchmark.calibrate import calibrate_run

    includes = _CALIB_FIXTURE / "includes.json"
    if not includes.exists():
        return JSONResponse({"available": False,
                             "reason": "no labeled calibration data available yet"})
    gold = json.loads(includes.read_text(encoding="utf-8"))
    result = calibrate_run(_CALIB_FIXTURE, gold, target_recall=0.99)
    payload = result.model_dump()
    payload["available"] = True
    payload["source"] = "fixture:real_run_min"
    return JSONResponse(payload)


@app.get("/ui/meta-analysis")
def ui_meta_analysis():
    """Meta-analysis payload (pooled estimates, indirect comparison, NMA) for the
    dashboard. Read-only; reads the completed review's analysis artifacts."""
    return JSONResponse(load_meta_analysis())


@app.get("/ui/meta-analysis/figure/{name}")
def ui_meta_analysis_figure(name: str):
    """Serve a forest/funnel PNG from the analysis figures dir (name-whitelisted)."""
    p = figure_path(name)
    if p is None:
        return JSONResponse({"error": "figure not found"}, status_code=404)
    return FileResponse(str(p), media_type="image/png")


# The product frontend (vanilla JS, no build step). html=True serves index.html
# at "/" and falls back to it for unknown paths. Mounted LAST so explicit routes win.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
