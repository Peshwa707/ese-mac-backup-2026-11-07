"""Human-readable run status for the ese driver CLI."""

from __future__ import annotations

from pathlib import Path

from core.sr.driver.plan import error_hint, planned_next_stage
from core.sr.orchestrator import engine
from core.sr.orchestrator.stages import topo_order


def run_status(run_dir: Path, *, cfg=None) -> dict:
    """Summarize where a run is in the orchestrator DAG."""
    from core.sr.orchestrator import state as st

    run_dir = Path(run_dir)
    try:
        state = st.load(run_dir)
    except FileNotFoundError:
        return {"error": "no run_state.json — not an orchestrated run", "run_dir": str(run_dir)}

    stages = engine.build_registry(cfg, frontend=_has_frontend(run_dir))
    order = topo_order(stages, state.profile)
    total = len(order)
    done = sum(1 for s in order if _st(state, s.name) in ("done", "approved"))
    blocked = []
    for s in order:
        status = _st(state, s.name)
        if status in ("awaiting_approval", "failed"):
            entry = {"name": s.name, "status": status}
            if status == "failed":
                ss = state.stages.get(s.name)
                entry["error_code"] = ss.error_code if ss else ""
                hint = error_hint(entry.get("error_code", ""))
                if hint:
                    entry["hint"] = hint
            blocked.append(entry)

    nxt, mode = planned_next_stage(state, order)

    gate = engine.export_gate(run_dir, cfg=cfg)
    return {
        "run_id": state.run_id,
        "profile": state.profile,
        "auto": state.auto,
        "progress": f"{done}/{total}",
        "current": nxt.name if nxt else None,
        "current_mode": mode if nxt else None,
        "blocked": blocked,
        "export_gate": gate,
        "stages": {s.name: _st(state, s.name) for s in order},
    }


def _st(state, name: str) -> str:
    ss = state.stages.get(name)
    return ss.status if ss else "pending"


def _has_frontend(run_dir: Path) -> bool:
    return (Path(run_dir) / "idea.txt").exists() or (Path(run_dir) / "pico.json").exists()


def format_status(status: dict) -> str:
    if status.get("error"):
        return status["error"]
    lines = [
        f"Run: {status['run_id']}  |  Profile: {status['profile']}",
        f"Progress: {status['progress']} stages complete",
    ]
    if status.get("current"):
        mode = status.get("current_mode")
        label = "Next (retry)" if mode == "retry" else "Next runnable"
        lines.append(f"{label}: {status['current']}")
    if status.get("blocked"):
        lines.append("Blocked:")
        for item in status["blocked"]:
            if isinstance(item, dict):
                name, st = item["name"], item["status"]
                lines.append(f"  - {name}: {st}")
                if item.get("error_code"):
                    lines.append(f"      error: {item['error_code']}")
                if item.get("hint"):
                    lines.append(f"      hint: {item['hint']}")
            else:
                name, st = item
                lines.append(f"  - {name}: {st}")
    gate = status.get("export_gate")
    if gate is None:
        lines.append("Export gate: legacy/ungated run dir")
    elif gate[0]:
        lines.append("Export gate: CLEAR — ese export may proceed after Reviewer-2 sign-off")
    else:
        lines.append("Export gate: BLOCKED")
        for r in gate[1]:
            lines.append(f"  - {r}")
    return "\n".join(lines)
