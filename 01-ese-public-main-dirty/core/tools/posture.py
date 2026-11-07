"""The maturity dial: cloud -> prefer-local -> local/harness, resolved per tool call.

The whole product's journey to running on free local AI is expressed as ONE setting
(the posture) plus each tool's own ``local_validated`` flag. This module answers a
single question: for THIS tool call, do we use the cloud brain or the local brain?
Safety tools (cloud_only) and any deliberate per-call override are honored, but a
cloud_only tool can never be forced local.
"""

from __future__ import annotations

from core.paths import env
from core.tools.spec import Tool

POSTURES = ("cloud", "prefer-local", "local", "harness")


def active_posture() -> str:
    p = env("EVIDENCE_POSTURE", default="cloud").lower()
    return p if p in POSTURES else "cloud"


def resolve_backend(tool: Tool, override: str | None = None,
                    posture: str | None = None) -> str:
    """Return 'cloud', 'local', or 'harness' for an AI tool, else 'n/a'.

    Order of precedence: deterministic -> n/a; explicit override (unless it would
    force a cloud_only tool local/harness); cloud_only -> cloud; then posture:
    ``cloud`` -> cloud, ``local`` -> local, ``harness`` -> harness only if enabled,
    ``prefer-local`` -> local only if the tool has earned it, else cloud.
    """
    if tool.kind != "ai":
        return "n/a"
    if override:
        override = override.lower()
        if override not in ("cloud", "local", "harness"):
            raise ValueError(f"override must be 'cloud', 'local', or 'harness', got {override!r}")
        if override in {"local", "harness"} and tool.backend.cloud_only:
            raise ValueError(
                f"{tool.name} is cloud-only (safety net) and cannot be forced {override}")
        if override == "harness" and not tool.backend.harness_enabled:
            raise ValueError(f"harness not yet supported for '{tool.name}'")
        return override
    if tool.backend.cloud_only:
        return "cloud"
    posture = (posture or active_posture()).lower()
    if posture == "cloud":
        return "cloud"
    if posture == "local":
        return "local"
    if posture == "harness":
        return "harness" if tool.backend.harness_enabled else "cloud"
    # prefer-local: a tool runs local only once its local version has earned it.
    return "local" if tool.backend.local_validated else "cloud"
