"""Load a completed review's meta-analysis artifacts for the frontend dashboard.

Product-shell support layer (no engine logic). Reads the analysis JSON a finished run
writes (pooled proportions/HRs, the anchored indirect comparison, the NMA league table)
plus the forest/funnel figures, and normalizes them into one payload the meta-analysis
view renders. Defaults to the mm-bcma-early-relapse vertical's configured output; the
base is injectable so it is testable without that run present.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_FIGURE_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.png$")   # no slashes/.. — path-traversal safe


def _analysis_dir(base: Path | None) -> Path | None:
    if base is not None:
        return Path(base)
    try:
        from core.sr.config import load_sr_config
        out = load_sr_config("mm-bcma-early-relapse").output_base
    except Exception:
        return None
    if not out:
        return None
    return Path(out).expanduser() / "06_analysis"


def _read_json(d: Path, name: str):
    p = d / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def load_meta_analysis(base: Path | None = None) -> dict:
    """Normalized meta-analysis payload. ``available`` is False (with a reason) when no
    completed analysis is found, so the dashboard shows a calm empty state."""
    d = _analysis_dir(base)
    if d is None or not d.is_dir():
        return {"available": False, "reason": "no completed meta-analysis found"}

    pooled = _read_json(d, "pooled_results.json") or {}
    indirect = _read_json(d, "indirect_comparison_results.json") or {}
    nma = _read_json(d, "nma_league_table.json") or {}

    fig_dir = d / "figures"
    figures = sorted(p.name for p in fig_dir.glob("*.png")) if fig_dir.is_dir() else []

    if not (pooled or indirect or nma or figures):
        return {"available": False, "reason": "analysis directory present but empty"}

    return {
        "available": True,
        "source": "mm-bcma-early-relapse",
        "proportions": pooled.get("proportions", []),
        "hazard_ratios": pooled.get("hazard_ratios", []),
        "indirect": indirect,
        "nma": nma,
        "figures": figures,
    }


def figure_path(name: str, base: Path | None = None) -> Path | None:
    """Resolve a figure name to a path INSIDE the analysis figures dir, or None.

    Rejects anything but a bare ``*.png`` filename and confirms the resolved path stays
    within the figures directory (defense in depth against path traversal)."""
    if not name or not _FIGURE_RE.match(name):
        return None
    d = _analysis_dir(base)
    if d is None:
        return None
    fig_dir = (d / "figures").resolve()
    candidate = (fig_dir / name).resolve()
    if fig_dir not in candidate.parents or not candidate.is_file():
        return None
    return candidate
