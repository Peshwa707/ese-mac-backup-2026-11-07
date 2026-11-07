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

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.api.app import app as api_app

app = FastAPI(title="Onco-Evidence Web", version="0.1.0")

# The engine's stable JSON contract, namespaced under /api.
app.mount("/api", api_app)

# The product frontend (vanilla JS, no build step). html=True serves index.html
# at "/" and falls back to it for unknown paths.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
