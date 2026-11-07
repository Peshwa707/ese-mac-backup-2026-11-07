"""Screening calibration harness (replicate-and-improve of otto-SR/srma ensemble_shift).

calibrate_screening is pure (records + gold set -> CalibrationResult), so the sweep,
operating-point selection and AUC are tested directly; one test runs the real labeled
fixture (4 known includes among 50 records) end to end.
"""
from __future__ import annotations

import json
from pathlib import Path

from benchmark.calibrate import calibrate_run, calibrate_screening, include_score
from core.sr.models import ScreenDecision

FIXTURE = Path(__file__).parent / "fixtures" / "benchmark" / "real_run_min"


def _rec(pmid, decision, conf=0.9, *, ek=1, votes=None):
    r = {"pmid": pmid, "decision": decision, "confidence": conf}
    if ek > 1:
        r["ensemble_k"] = ek
        r["include_votes"] = votes
    return r


def test_include_score_is_monotone_in_include_likelihood():
    assert include_score(_rec("a", "include", 0.95)) == 0.95
    assert abs(include_score(_rec("b", "exclude", 0.95)) - 0.05) < 1e-9   # confident EXCLUDE scores LOW
    assert include_score(_rec("c", "uncertain", 0.4)) == 0.5
    # ensemble vote fraction overrides the single-pass mapping
    assert include_score(_rec("d", "include", 0.1, ek=10, votes=7)) == 0.7


def test_calibrate_fixture_meets_target_recall():
    gold = json.loads((FIXTURE / "includes.json").read_text())
    res = calibrate_run(FIXTURE, gold, target_recall=0.99)
    assert res.n_positive == 4
    assert res.n_negative == 46
    assert res.score_axis == "decision_confidence"
    assert res.target_met is True
    assert res.achieved_sensitivity >= 0.99            # never miss an includable study
    assert 0.0 <= res.auc <= 1.0
    assert res.chosen_threshold is not None
    assert res.curve                                   # non-empty sweep


def test_operating_point_is_most_specific_meeting_target():
    # Perfectly separable: positives score >= 0.8, negatives <= 0.7.
    recs = [
        _rec("P1", "include", 0.1, ek=10, votes=9),   # 0.9  gold
        _rec("P2", "include", 0.1, ek=10, votes=8),   # 0.8  gold
        _rec("N1", "include", 0.1, ek=10, votes=7),   # 0.7
        _rec("N2", "exclude", 0.1, ek=10, votes=3),   # 0.3
        _rec("N3", "exclude", 0.1, ek=10, votes=2),   # 0.2
    ]
    res = calibrate_screening(recs, ["P1", "P2"], target_recall=1.0)
    assert res.score_axis == "vote_fraction"
    assert res.target_met is True
    assert res.chosen_threshold == 0.8                 # keeps both positives...
    assert res.achieved_sensitivity == 1.0
    assert res.achieved_specificity == 1.0             # ...and excludes every negative
    assert res.auc == 1.0                              # perfect separation


def test_degenerate_labels_select_no_operating_point():
    res = calibrate_screening([_rec("x", "include", 0.9)], ["x"])  # no negatives
    assert res.target_met is False
    assert res.chosen_threshold is None
    assert any("positive and negative" in c for c in res.caveats)
