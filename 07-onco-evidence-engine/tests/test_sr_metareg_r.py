"""R-gated checks for Phase 3b meta-regression."""

from __future__ import annotations

import pytest

from core.sr.models import ExtractedStudy, ExtractedValue
from core.sr.stats.meta import metacont_available, metareg_available, run_metacont, run_metareg
from core.sr.stats.prepare import ContRow, MetaRegRow, hedges_g_effect, metareg_rows


@pytest.mark.skipif(not metareg_available(), reason="R package 'metafor' not installed")
def test_run_metareg_known_moderator_signal():
    rows = [
        MetaRegRow(str(i), yi=0.10 + 0.06 * i, vi=0.01, moderator=float(i))
        for i in range(10)
    ]
    result = run_metareg(rows, "age", min_k=10)
    assert result.available is True
    assert result.reliable is True
    term = next(t for t in result.terms if t.term == "age")
    assert term.estimate > 0
    assert term.pval < 0.05
    assert result.QM_p is not None and result.QM_p < 0.05


@pytest.mark.skipif(not (metareg_available() and metacont_available()),
                    reason="R packages 'metafor' and 'meta' not installed")
def test_python_smd_for_metareg_matches_metacont_smd(tmp_path):
    rows = [
        ContRow("A", "ALL", n_tx=40, mean_tx=1.2, sd_tx=0.4,
                n_ctrl=38, mean_ctrl=0.9, sd_ctrl=0.5),
        ContRow("B", "ALL", n_tx=40, mean_tx=1.2, sd_tx=0.4,
                n_ctrl=38, mean_ctrl=0.9, sd_ctrl=0.5),
    ]
    py_smd, _ = hedges_g_effect(rows[0])
    pooled = run_metacont(rows, "Quality of life", tmp_path / "forest.png")
    overall = next(r for r in pooled if r.group == "ALL")
    assert overall.estimate == pytest.approx(py_smd, rel=0.05)


@pytest.mark.skipif(not metareg_available(), reason="R package 'metafor' not installed")
def test_metareg_rows_continuous_smd_feeds_r_model():
    ext = []
    for i in range(10):
        ext.append(ExtractedStudy(pmid=str(i), values=[
            ExtractedValue(field="tx_n", group="continuous", value="40"),
            ExtractedValue(field="tx_mean", group="continuous", value=str(1.0 + i * 0.05)),
            ExtractedValue(field="tx_sd", group="continuous", value="0.4"),
            ExtractedValue(field="ctrl_n", group="continuous", value="40"),
            ExtractedValue(field="ctrl_mean", group="continuous", value="0.8"),
            ExtractedValue(field="ctrl_sd", group="continuous", value="0.5"),
            ExtractedValue(field="age", group="moderator", value=str(50 + i)),
        ]))
    rows, excluded = metareg_rows(
        ext, "qol", "age", {},
        outcome_kind="continuous",
        continuous_pair={"n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
                         "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd"},
    )
    assert excluded == 0 and len(rows) == 10
    result = run_metareg(rows, "age", min_k=10)
    assert result.available is True
    assert result.reliable is True
