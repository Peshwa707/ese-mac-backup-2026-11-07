"""Sensitivity + publication-bias diagnostics: honest gating + live behavior."""

from pathlib import Path

import pytest

from core.sr.stats.diagnostics import run_effect_diagnostics
from core.sr.stats.meta import r_available
from core.sr.stats.prepare import HRRow


def _rows(n):
    # n studies with mild positive effects and VARYING precision (so Egger/Begg,
    # which regress on the standard error, have variation to fit).
    out = []
    for i in range(n):
        vi = 0.01 + 0.01 * (i % 5)          # precisions differ across studies
        yi = 0.30 + 0.04 * ((i % 4) - 1.5)  # effects scatter around logRR~0.3
        out.append(HRRow(pmid=str(i), modality="x", hr=round(2.718 ** yi, 3), yi=yi, vi=vi))
    return out


def test_too_few_studies_no_fabrication(tmp_path: Path):
    d = run_effect_diagnostics(_rows(2), "eff", "Effect", tmp_path)
    assert d.k == 2 and "not performed" in d.pubbias_note
    assert d.leave_one_out == [] and d.egger_p is None


@pytest.mark.skipif(not r_available(), reason="R/metafor not installed")
def test_loo_runs_pubbias_gated_below_10(tmp_path: Path):
    d = run_effect_diagnostics(_rows(6), "eff", "Effect", tmp_path)
    assert d.k == 6
    assert len(d.leave_one_out) == 6          # leave-one-out runs at k>=3
    assert d.egger_p is None                  # publication bias gated at k<10
    assert "k=6" in d.pubbias_note and "<10" in d.pubbias_note


@pytest.mark.skipif(not r_available(), reason="R/metafor not installed")
def test_pubbias_performed_at_k10(tmp_path: Path):
    d = run_effect_diagnostics(_rows(12), "eff", "Effect", tmp_path)
    assert d.k == 12 and d.pubbias_note == "performed"
    assert d.egger_p is not None and d.begg_p is not None  # tests run at k>=10
