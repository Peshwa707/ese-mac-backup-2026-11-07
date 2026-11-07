"""Human-in-the-loop conclusion lock.

Final conclusions, GRADE certainty, and clinical recommendations are LOCKED until a
human reviewer-of-record records a :class:`ReviewerSignoff`. Until then the renderers
substitute a DRAFT banner — the computed values stay in the serialized data so the
reviewer can see and approve them, but they are never presented as final output.

This is the structural safeguard behind the independent human verification gate:
it is enforced by code, not merely documented.
"""

from __future__ import annotations

from core.sr.models import ReviewerSignoff

DRAFT_CERTAINTY = "DRAFT — awaiting human sign-off"
DRAFT_BANNER = (
    "CONCLUSIONS LOCKED — awaiting human sign-off. Final conclusions, GRADE certainty, "
    "and recommendations are withheld until a reviewer-of-record approves them "
    "(record approval with --approve-conclusions \"<reviewer>\")."
)


def is_locked(run) -> bool:
    """True when no human sign-off has been recorded (conclusions must render as DRAFT)."""
    return getattr(run, "reviewer_signoff", None) is None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def approve(run, reviewer: str, *, timestamp: str = "", scope: str = "conclusions",
            note: str = "") -> ReviewerSignoff:
    """Record human sign-off on ``run``, unlocking conclusions for rendering.

    ``reviewer`` (a name/initials) is required — an empty value raises, so a sign-off
    can never be recorded anonymously.
    """
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer name/initials required to approve conclusions")
    signoff = ReviewerSignoff(reviewer=reviewer.strip(), timestamp=timestamp or _now_iso(),
                              scope=scope, note=note)
    run.reviewer_signoff = signoff
    return signoff
