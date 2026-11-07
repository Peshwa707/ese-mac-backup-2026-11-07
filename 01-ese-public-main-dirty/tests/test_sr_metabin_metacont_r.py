"""R-gated checks for Phase 3 binary and continuous pooling."""

from __future__ import annotations

import pytest

from core.sr.stats.meta import metabin_available, metacont_available, run_metabin, run_metacont
from core.sr.stats.prepare import BinRow, ContRow


@pytest.mark.skipif(not metabin_available(), reason="R package 'meta' not installed")
def test_run_metabin_rr_and_configured_number_needed(tmp_path):
    rows = [
        BinRow("A", "ALL", events_tx=80, n_tx=100, events_ctrl=20, n_ctrl=100),
        BinRow("B", "ALL", events_tx=75, n_tx=100, events_ctrl=20, n_ctrl=100),
    ]
    res = run_metabin(rows, "Response", tmp_path / "forest.png", beneficial=True)
    overall = next(r for r in res if r.group == "ALL")
    assert overall.measure == "RR"
    assert overall.estimate == pytest.approx(3.9, rel=0.20)
    assert overall.rd == pytest.approx(0.58, abs=0.08)
    assert overall.number_needed.label == "NNT"
    assert overall.number_needed.value == pytest.approx(2, rel=0.75)
    assert overall.pi_lb is None and overall.pi_ub is None
    assert overall.ci_ub > overall.ci_lb


@pytest.mark.skipif(not metacont_available(), reason="R package 'meta' not installed")
def test_run_metacont_smd_hksj_columns_and_k2_prediction_interval(tmp_path):
    rows = [
        ContRow("A", "ALL", n_tx=40, mean_tx=1.2, sd_tx=0.4,
                n_ctrl=38, mean_ctrl=0.9, sd_ctrl=0.5),
        ContRow("B", "ALL", n_tx=36, mean_tx=1.1, sd_tx=0.4,
                n_ctrl=36, mean_ctrl=0.8, sd_ctrl=0.5),
    ]
    res = run_metacont(rows, "Quality of life", tmp_path / "forest.png")
    overall = next(r for r in res if r.group == "ALL")
    assert overall.measure == "SMD"
    assert overall.estimate > 0
    assert overall.ci_ub > overall.ci_lb
    assert hasattr(overall, "Q") and hasattr(overall, "tau2")
    assert overall.pi_lb is None and overall.pi_ub is None
