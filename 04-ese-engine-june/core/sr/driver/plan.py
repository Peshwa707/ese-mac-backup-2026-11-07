"""Shared stage-planning helpers for the ese driver."""

from __future__ import annotations

from core.sr.orchestrator.stages import Stage, next_stage


def earliest_failed_stage(state, order: list[Stage]) -> Stage | None:
    for s in order:
        ss = state.stages.get(s.name)
        if ss and ss.status == "failed":
            return s
    return None


def planned_next_stage(state, order: list[Stage]) -> tuple[Stage | None, str]:
    """What ``ese next`` will run without mutating state.

    Returns ``(stage, mode)`` where mode is ``retry`` for a failed stage or
    ``pending`` for the next runnable pending/stale stage.
    """
    failed = earliest_failed_stage(state, order)
    if failed is not None:
        return failed, "retry"
    nxt = next_stage(state, order)
    return nxt, "pending"


def reset_earliest_failed(run_dir, state, order: list[Stage]) -> bool:
    """Reset the earliest failed stage to pending. Returns True if one was reset."""
    from core.sr.orchestrator import state as st

    for s in order:
        ss = state.stages.get(s.name)
        if ss and ss.status == "failed":
            ss.status = "pending"
            ss.error_code = ""
            st.save(run_dir, state)
            return True
    return False


_ERROR_HINTS = {
    "MissingKeyError": (
        "Set ANTHROPIC_API_KEY in your environment or add it to .env in the repo root "
        "(see docs/GETTING_STARTED.md). For a persistent key file: "
        "~/.config/ese/providers.env mode 0600 with ANTHROPIC_API_KEY=..."
    ),
}


def error_hint(error_code: str) -> str:
    base = (error_code or "").split()[0]
    return _ERROR_HINTS.get(base, "")
