"""Tests for dose-response meta-analysis: honesty gate + (live) dosresmeta fit."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.sr.stats import dose_response as dr
from core.sr.stats.dose_response import (
    DoseLevel,
    DoseStudy,
    assess_dose_feasibility,
    build_dose_data,
    dose_response_available,
    run_dose_response,
)


def _study(name, unit, doses):
    return DoseStudy(name, "CAR-T", unit, [DoseLevel(d, e, n) for d, e, n in doses])


_FEASIBLE = [
    _study("S1", "u", [(0.5, 5, 20), (1.0, 9, 20), (2.0, 14, 20), (4.0, 17, 20)]),
    _study("S2", "u", [(0.75, 6, 25), (1.5, 12, 25), (3.0, 18, 25), (6.0, 22, 25)]),
    _study("S3", "u", [(0.5, 4, 18), (1.0, 8, 18), (2.5, 13, 18), (5.0, 16, 18)]),
]


def test_build_dose_data_honest_empty():
    assert build_dose_data([], {}, None) == []          # no table -> no fabricated tiers
    assert build_dose_data([], {}, {}) == []


def test_build_dose_data_from_table():
    table = {"111": {"unit": "x10^6/kg", "levels": [{"dose": 0.5, "events": 5, "n": 20},
                                                     {"dose": 2.0, "events": 14, "n": 20}]}}
    studies = build_dose_data([], {"111": "CAR-T"}, table)
    assert len(studies) == 1 and len(studies[0].levels) == 2 and studies[0].dose_unit == "x10^6/kg"


def test_feasibility_reasons():
    assert assess_dose_feasibility([])[0] is False and "no per-dose" in assess_dose_feasibility([])[1].lower()
    two_levels = [_study(f"S{i}", "u", [(1, 5, 20), (2, 10, 20)]) for i in range(3)]
    ok, reason, _ = assess_dose_feasibility(two_levels)
    assert not ok and "distinct dose levels" in reason
    ok2, reason2, unit2 = assess_dose_feasibility(_FEASIBLE)
    assert ok2 and unit2 == "u"


def test_run_infeasible_without_data():
    res = run_dose_response([], "orr", Path("/tmp/dr"))
    assert res.feasible is False and "no per-dose" in res.reason.lower() and res.curve == []


def test_run_reports_missing_module_not_curve(monkeypatch, tmp_path):
    monkeypatch.setattr(dr, "dose_response_available", lambda: False)
    res = run_dose_response(_FEASIBLE, "orr", tmp_path)
    assert res.feasible is False and "module not installed" in res.reason and res.curve == []


@pytest.mark.skipif(not dose_response_available(),
                    reason="dosresmeta/rms not installed (set SR_RSCRIPT to a CRAN R with them)")
def test_live_dosresmeta_fits_curve(tmp_path):
    res = run_dose_response(_FEASIBLE, "orr", tmp_path)
    assert res.feasible, res.reason
    assert len(res.curve) >= 10
    assert res.curve[0]["est"] == pytest.approx(1.0, abs=0.05)     # reference RR = 1
    assert res.curve[-1]["est"] > res.curve[0]["est"]              # rising dose-response
