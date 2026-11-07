"""Bearer-token authentication dependency for the Evidence Engine API.

Behaviour (spec §6 Phase-5 hardening):
  - If env var EVIDENCE_API_TOKEN (alias: ONCO_API_TOKEN) is SET every request
    must supply a matching ``Authorization: Bearer <token>`` header.  Missing or
    wrong → HTTP 401 with a ``WWW-Authenticate: Bearer`` challenge header.
  - If it is UNSET the app runs in dev mode: every request is
    allowed and a one-time warning is emitted so the operator knows auth is
    off.

The token is read from the environment at *request time* (not import time) so
that test code can monkeypatch ``os.environ`` reliably.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Header, HTTPException
from fastapi.security.utils import get_authorization_scheme_param

logger = logging.getLogger(__name__)

# Sentinel so we only emit the dev-mode warning once per process lifetime.
_warned_no_auth: bool = False


async def auth_ctx(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """FastAPI dependency that enforces bearer-token auth when configured."""
    global _warned_no_auth

    from core.paths import env
    expected_token = env("EVIDENCE_API_TOKEN", "ONCO_API_TOKEN")

    if not expected_token:
        # Dev mode — allow the request but warn once.
        if not _warned_no_auth:
            logging.warning(
                "EVIDENCE_API_TOKEN is not set — authentication is DISABLED. "
                "Set this env var before deploying to production."
            )
            _warned_no_auth = True
        return {"user": "anonymous"}

    # Production mode — validate the supplied token.
    scheme, credentials = get_authorization_scheme_param(authorization or "")
    if not authorization or scheme.lower() != "bearer" or credentials != expected_token:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {"user": "token"}
