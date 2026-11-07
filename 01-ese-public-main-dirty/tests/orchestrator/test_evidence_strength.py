"""WP-M4.1 — evidence-strength gate (§7b)."""
import json

from core.sr.orchestrator import evidence_strength as es
from core.sr.orchestrator.stages import RunContext


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


_STATS = {
    "available": True,
    "outcomes": [
        {"field": "orr", "results": [
            {"group": "ALL", "k": 34, "prop": 82.6, "I2": 90.0},      # high I2 -> suppress
            {"group": "CAR-T", "k": 6, "prop": 88.0, "I2": 20.0}]},   # ok-ish but single-arm
    ],
    "hr_outcomes": [
        {"field": "pfs_hr", "results": [
            {"group": "ALL", "k": 4, "hr": 0.45, "I2": 20.0},          # ok -> not suppressed
            {"group": "BsAb", "k": 2, "hr": 0.40, "I2": 0.0}]},        # k<3 -> suppress
    ],
}


def _by(report, outcome, group):
    return next(f for f in report["flags"] if f["outcome"] == outcome and f["group"] == group)


def test_high_heterogeneity_proportion_suppressed_and_non_comparative():
    rep = es.assess(_STATS)
    orr_all = _by(rep, "orr", "ALL")
    assert orr_all["suppress_headline"] is True and orr_all["require_prediction_interval"] is True
    assert orr_all["non_comparative"] is True
    assert any("heterogeneity" in r for r in orr_all["reasons"])


def test_proportions_always_non_comparative():
    rep = es.assess(_STATS)
    for f in rep["flags"]:
        if f["kind"] == "proportion":
            assert f["non_comparative"] is True
        if f["kind"] == "hazard_ratio":
            assert f["non_comparative"] is False


def test_clean_hr_not_suppressed_but_low_k_is():
    rep = es.assess(_STATS)
    assert _by(rep, "pfs_hr", "ALL")["suppress_headline"] is False     # I2 20, k 4
    low = _by(rep, "pfs_hr", "BsAb")
    assert low["suppress_headline"] is True and any("floor" in r for r in low["reasons"])


def test_thresholds_configurable():
    # tightening the I2 ceiling suppresses the otherwise-ok CAR-T proportion
    rep = es.assess(_STATS, i2_max=10.0)
    assert _by(rep, "orr", "CAR-T")["suppress_headline"] is True


def test_missing_i2_fails_closed_even_at_k_floor():
    # I2 absent / None with k at the floor must STILL suppress (heterogeneity unverifiable)
    stats = {"hr_outcomes": [{"field": "os_hr", "results": [
        {"group": "ALL", "k": 5},                          # I2 absent
        {"group": "BsAb", "k": 4, "I2": None},             # I2 None
        {"group": "CAR-T", "k": 6, "I2": float("nan")},    # I2 NaN (unverifiable)
        {"group": "INF", "k": 6, "I2": float("inf")}]}]}    # I2 inf (unverifiable)
    rep = es.assess(stats)
    for f in rep["flags"]:
        assert f["suppress_headline"] is True
        assert any("unavailable" in r for r in f["reasons"])


def test_adapter_writes_report(tmp_path):
    (tmp_path / "stats.json").write_text(json.dumps(_STATS))
    res = es.evidence_strength_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    rep = json.loads((tmp_path / "evidence_strength.json").read_text())
    assert rep["n_suppressed"] >= 2 and rep["n_non_comparative"] >= 2
    assert "stats.json" in res.deterministic_inputs
