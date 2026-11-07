"""Phase 1a: human-in-the-loop conclusion hard-lock.

Final conclusions + GRADE certainty must render as DRAFT until a human reviewer-of-record
records a sign-off; a recorded sign-off unlocks them. The computed certainty value
always stays in the data (only the *rendered* output is withheld).
"""

from __future__ import annotations

import pytest

from core.sr.config import load_sr_config
from core.sr.manuscript.assemble import _grade_sof, _title_abstract
from core.sr.models import GradeRow
from core.sr.signoff import DRAFT_BANNER, DRAFT_CERTAINTY, approve, is_locked
from tests.test_sr_manuscript import _synthetic_run

_NARRATIVE = {
    "abstract_background": "BG.",
    "abstract_results": "ORR was 84%.",
    "abstract_conclusions": "X is effective and should be preferred.",
    "introduction": "Intro.",
    "discussion": "Discussion prose.",
}


def _grade_rows():
    return [GradeRow(outcome="Overall response rate (ORR)", field="orr", n_studies=4,
                     n_rct=2, start="High", downgrades=[], certainty="Moderate")]


def test_is_locked_until_signoff():
    run = _synthetic_run()
    assert is_locked(run) is True
    approve(run, "Dr. Reviewer", timestamp="2026-06-08T00:00:00+00:00")
    assert is_locked(run) is False
    assert run.reviewer_signoff.reviewer == "Dr. Reviewer"


def test_approve_requires_a_reviewer_name():
    run = _synthetic_run()
    with pytest.raises(ValueError):
        approve(run, "   ")
    assert is_locked(run) is True


def test_grade_sof_withholds_certainty_when_locked():
    rows = _grade_rows()
    # Locked: certainty column shows DRAFT, NOT the computed "Moderate".
    locked_tbl = next(b for b in _grade_sof(rows, locked=True).blocks if hasattr(b, "rows"))
    assert locked_tbl.rows[0][3] == DRAFT_CERTAINTY
    # Unlocked: the real computed certainty renders.
    open_tbl = next(b for b in _grade_sof(rows, locked=False).blocks if hasattr(b, "rows"))
    assert open_tbl.rows[0][3] == "Moderate"
    # The computed value is never lost from the data regardless of lock state.
    assert rows[0].certainty == "Moderate"


def test_abstract_conclusions_locked_until_signoff():
    run, cfg = _synthetic_run(), load_sr_config()
    comp = _title_abstract(run, cfg, _NARRATIVE)
    texts = " ".join(getattr(b, "text", "") for b in comp.blocks)
    assert DRAFT_BANNER in texts
    assert "should be preferred" not in texts          # final conclusion withheld

    approve(run, "JS")
    comp2 = _title_abstract(run, cfg, _NARRATIVE)
    texts2 = " ".join(getattr(b, "text", "") for b in comp2.blocks)
    assert "should be preferred" in texts2             # unlocked after sign-off
    assert DRAFT_BANNER not in texts2
