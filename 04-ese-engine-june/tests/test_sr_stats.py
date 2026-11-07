"""Offline tests for the meta-analysis data layer (parse, prepare, graceful R-absent)."""

from __future__ import annotations

import pytest

from core.sr.models import ExtractedStudy, ExtractedValue
from core.sr.stats import run as stats_run
from core.sr.stats.impute import (
    mean_sd_from_median_iqr,
    mean_sd_from_median_range,
    sd_from_ci,
    sd_from_range,
    sd_from_se,
)
from core.sr.stats.meta import MetaRegResult, MetaRegTerm, interpret_number_needed, run_metareg
from core.sr.stats.prepare import (
    MetaRegRow,
    bin_rows,
    cont_rows,
    hedges_g_effect,
    metareg_rows,
    outcome_rows,
    parse_int,
    parse_number,
    parse_pct,
    parse_proportion,
)


def test_parse_pct():
    assert parse_pct("84.6%") == 84.6
    assert parse_pct("ORR was 84%") == 84.0
    assert parse_pct("ORR 84.6% (95% CI 78-90)") == 84.6
    assert parse_pct("84 of 110") == 76.4
    assert parse_pct("84/110") == 76.4
    assert parse_pct("0.85") == 85.0
    assert parse_pct("no percent here") is None
    assert parse_pct("150%") is None          # out of range rejected
    assert parse_pct("65.9% vs 16.8%") is None
    assert parse_pct("12 and 18") is None


def test_parse_proportion_recovers_events_and_denominator():
    parsed = parse_proportion("84 of 110")
    assert parsed is not None
    assert (parsed.events, parsed.n, parsed.pct) == (84, 110, 76.4)


def test_parse_int():
    assert parse_int("648") == 648
    assert parse_int("Komodo: 648 (124 with...)") == 648
    assert parse_int("1,234 patients") == 1234
    assert parse_int("none") is None


def test_parse_number():
    assert parse_number("mean -1.25") == -1.25
    assert parse_number("1,234.5") == 1234.5
    assert parse_number("NR") is None


def test_imputation_helpers_hand_checks_and_invalid_inputs():
    assert sd_from_se(2.0, 25) == pytest.approx(10.0)
    assert sd_from_ci(8.04, 11.96, 100) == pytest.approx(10.0, rel=0.01)
    assert sd_from_range(0, 12, 10) == pytest.approx(12 / (12 ** 0.5))
    assert sd_from_range(0, 12, 40) == pytest.approx(3.0)
    assert sd_from_range(0, 12, 100) == pytest.approx(2.0)
    assert sd_from_se(1.0, 1) is None
    assert sd_from_ci(4, 2, 10) is None
    assert sd_from_range(4, 2, 10) is None


def test_median_summary_imputation_helpers():
    iqr = mean_sd_from_median_iqr(10, 8, 14, 50)
    assert iqr is not None
    assert iqr[0] == pytest.approx(10.6667, rel=0.001)
    assert iqr[1] == pytest.approx(4.58, rel=0.02)
    assert "median/IQR" in iqr[2]
    rng = mean_sd_from_median_range(10, 4, 20, 20)
    assert rng is not None
    assert rng[0] == pytest.approx(11.0)
    assert rng[1] == pytest.approx(4.0)
    assert "median/range" in rng[2]
    assert mean_sd_from_median_iqr(10, 12, 14, 50) is None
    assert mean_sd_from_median_range(10, 12, 20, 20) is None


def _study(pmid, modality, orr=None, n=None):
    vals = []
    if orr is not None:
        vals.append(ExtractedValue(field="orr", group="efficacy", value=orr))
    if n is not None:
        vals.append(ExtractedValue(field="total_n", group="population", value=n))
    return ExtractedStudy(pmid=pmid, modality=modality, values=vals)


def _study_values(pmid, modality, values):
    return ExtractedStudy(
        pmid=pmid,
        modality=modality,
        values=[ExtractedValue(field=k, group="fixture", value=v) for k, v in values.items()],
    )


def test_outcome_rows_pools_parseable_and_counts_excluded():
    ext = [
        _study("A", "CAR-T", orr="84%", n="100"),       # poolable -> events 84
        _study("B", "BsAb", orr="60%", n="NR"),         # no denom -> excluded
        _study("C", "CAR-T", orr="NR", n="50"),         # outcome NR -> absent (not excluded)
    ]
    rows, excluded = outcome_rows(ext, "orr", {})
    assert [(r.pmid, r.events, r.n, r.modality) for r in rows] == [("A", 84, 100, "CAR-T")]
    assert excluded == 1


def test_outcome_rows_uses_embedded_events_and_excludes_ambiguous_values():
    ext = [
        _study("A", "CAR-T", orr="84 of 110", n=None),
        _study("B", "BsAb", orr="0.85", n="200"),
        _study("C", "CAR-T", orr="65.9% vs 16.8%", n="100"),
    ]
    rows, excluded = outcome_rows(ext, "orr", {})
    assert [(r.pmid, r.events, r.n, r.pct) for r in rows] == [
        ("A", 84, 110, 76.4),
        ("B", 170, 200, 85.0),
    ]
    assert excluded == 1


def test_bin_rows_parse_clean_rows_and_exclude_partial_pairs():
    pair = {
        "events_tx": "tx_events", "n_tx": "tx_n",
        "events_ctrl": "ctrl_events", "n_ctrl": "ctrl_n",
    }
    ext = [
        _study_values("A", "Drug", {"tx_events": "30", "tx_n": "100",
                                    "ctrl_events": "20", "ctrl_n": "100"}),
        _study_values("B", "Drug", {"tx_events": "12", "tx_n": "50",
                                    "ctrl_events": "NR", "ctrl_n": "50"}),
        _study_values("C", "Drug", {"orr": "80%"}),
    ]
    rows, excluded = bin_rows(ext, pair, {})
    assert [(r.pmid, r.events_tx, r.n_tx, r.events_ctrl, r.n_ctrl) for r in rows] == [
        ("A", 30, 100, 20, 100)
    ]
    assert excluded == 1


def test_cont_rows_parse_clean_rows_and_exclude_missing_cells():
    pair = {
        "n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
        "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd",
    }
    ext = [
        _study_values("A", "Drug", {"tx_n": "40", "tx_mean": "1.2", "tx_sd": "0.4",
                                    "ctrl_n": "38", "ctrl_mean": "0.9", "ctrl_sd": "0.5"}),
        _study_values("B", "Drug", {"tx_n": "40", "tx_mean": "1.2", "tx_sd": "NR",
                                    "ctrl_n": "38", "ctrl_mean": "0.9", "ctrl_sd": "0.5"}),
    ]
    rows, excluded = cont_rows(ext, pair, {})
    assert [(r.pmid, r.n_tx, r.mean_tx, r.sd_ctrl) for r in rows] == [("A", 40, 1.2, 0.5)]
    assert excluded == 1


def test_cont_rows_imputes_median_iqr_and_excludes_unusable_spread():
    pair = {
        "n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
        "median_tx": "tx_median", "q1_tx": "tx_q1", "q3_tx": "tx_q3",
        "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd",
        "median_ctrl": "ctrl_median", "q1_ctrl": "ctrl_q1", "q3_ctrl": "ctrl_q3",
    }
    ext = [
        _study_values("A", "Drug", {"tx_n": "50", "tx_median": "10", "tx_q1": "8", "tx_q3": "14",
                                    "ctrl_n": "48", "ctrl_median": "7", "ctrl_q1": "5", "ctrl_q3": "9"}),
        _study_values("B", "Drug", {"tx_n": "50", "tx_median": "10",
                                    "ctrl_n": "48", "ctrl_median": "7"}),
    ]
    rows, excluded = cont_rows(ext, pair, {})
    assert len(rows) == 1 and excluded == 1
    assert rows[0].imputed is True
    assert "median/IQR" in rows[0].imputation_method
    assert rows[0].mean_tx == pytest.approx(10.6667, rel=0.001)


def test_cont_rows_imputes_median_range():
    pair = {
        "n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
        "median_tx": "tx_median", "lo_tx": "tx_lo", "hi_tx": "tx_hi",
        "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd",
        "median_ctrl": "ctrl_median", "lo_ctrl": "ctrl_lo", "hi_ctrl": "ctrl_hi",
    }
    ext = [_study_values("A", "Drug", {"tx_n": "20", "tx_median": "10", "tx_lo": "4", "tx_hi": "20",
                                       "ctrl_n": "20", "ctrl_median": "8", "ctrl_lo": "2",
                                       "ctrl_hi": "14"})]
    rows, excluded = cont_rows(ext, pair, {})
    assert excluded == 0 and rows[0].imputed is True
    assert "median/range" in rows[0].imputation_method


def test_hedges_g_effect_matches_planned_smd_formula():
    rows, _ = cont_rows([
        _study_values("A", "Drug", {"tx_n": "40", "tx_mean": "1.2", "tx_sd": "0.4",
                                    "ctrl_n": "38", "ctrl_mean": "0.9", "ctrl_sd": "0.5"}),
    ], {"n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
        "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd"}, {})
    yi, vi = hedges_g_effect(rows[0])
    assert yi == pytest.approx(0.657, rel=0.02)
    assert vi == pytest.approx(0.0536, rel=0.02)


def test_metareg_rows_for_log_effect_and_continuous():
    ext = [
        _study_values("A", "Drug", {"hr": "HR 0.80 (95% CI 0.70-0.90)", "age": "60",
                                    "tx_n": "40", "tx_mean": "1.2", "tx_sd": "0.4",
                                    "ctrl_n": "38", "ctrl_mean": "0.9", "ctrl_sd": "0.5"}),
        _study_values("B", "Drug", {"hr": "HR 0.90 (95% CI 0.80-1.00)", "age": "NR",
                                    "tx_n": "40", "tx_mean": "1.2", "tx_sd": "0.4",
                                    "ctrl_n": "38", "ctrl_mean": "0.9", "ctrl_sd": "0.5"}),
    ]
    log_rows, log_excluded = metareg_rows(ext, "hr", "age", {}, outcome_kind="log_effect")
    assert len(log_rows) == 1 and log_excluded == 1
    cont_rows_out, cont_excluded = metareg_rows(
        ext, "qol", "age", {}, outcome_kind="continuous",
        continuous_pair={"n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
                         "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd"})
    assert len(cont_rows_out) == 1 and cont_excluded == 1
    assert cont_rows_out[0].yi == pytest.approx(hedges_g_effect(
        cont_rows(ext[:1], {"n_tx": "tx_n", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
                            "n_ctrl": "ctrl_n", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd"}, {})[0][0]
    )[0])


def test_run_metareg_k_floor_cannot_be_lowered():
    rows = [MetaRegRow(str(i), yi=0.1 * i, vi=0.05, moderator=float(i)) for i in range(3)]
    result = run_metareg(rows, "age", min_k=2)
    assert result.available is False
    assert result.reliable is False
    assert "k<4" in result.note


def test_number_needed_interpretation_is_config_driven():
    assert interpret_number_needed(0.10, 0.04, 0.16, True).label == "NNT"
    assert interpret_number_needed(-0.10, -0.16, -0.04, True).label == "NNH"
    assert interpret_number_needed(-0.10, -0.16, -0.04, False).label == "NNT"
    assert interpret_number_needed(0.10, 0.04, 0.16, False).label == "NNH"
    neutral = interpret_number_needed(0.10, 0.04, 0.16, None)
    assert neutral.label == "NNC" and "not configured" in neutral.note


def test_number_needed_not_estimable_when_rd_ci_crosses_zero():
    nn = interpret_number_needed(0.10, -0.02, 0.20, True)
    assert nn.label == "not estimable"
    assert nn.value is None


def test_meta_analyze_graceful_when_r_absent(monkeypatch):
    class Cfg:
        subgroup_field = ""
        subgroup_map = None
        dose_outcome = ""
        synthesis_arms = []

        def poolable_fields(self):
            return {"proportion": [("orr", "Response")], "log_effect": []}

        def binary_2x2_fields(self):
            return []

        def continuous_fields(self):
            return []

    monkeypatch.setattr(stats_run, "r_available", lambda: False)
    res = stats_run.meta_analyze([_study("A", "CAR-T", "84%", "100")], {},
                                 tmp_dir := __import__("pathlib").Path("/tmp"), cfg=Cfg())
    assert res.available is False and "metafor" in res.note


class _AnchoredCfg:
    """A config that declares an anchored indirect comparison with a log-effect (HR)
    outcome but NO anchor terms — the fail-open case from the round-3 review."""
    subgroup_field = ""
    subgroup_map = None
    dose_outcome = ""
    synthesis_arms = []
    indirect_comparison = True
    anchor_terms: list = []

    def poolable_fields(self):
        return {"proportion": [], "log_effect": [("pfs_hr", "PFS HR")]}

    def binary_2x2_fields(self):
        return []

    def continuous_fields(self):
        return []


def test_anchored_config_without_anchor_terms_fails_closed_no_hr_pool(tmp_path):
    # Round-3 BLOCKER: indirect_comparison=True + empty anchor_terms must SUPPRESS HR
    # pooling (fail closed), never emit an unvalidated-comparator hazard-ratio pool.
    ext = [_study_values("A", "CAR-T", {"pfs_hr": "0.50 (0.30-0.80)"}),
           _study_values("B", "BsAb", {"pfs_hr": "0.60 (0.40-0.90)"})]
    res = stats_run.meta_analyze(ext, {}, tmp_path, cfg=_AnchoredCfg())
    assert res.hr_outcomes == []                       # no HR pool produced
    assert "SUPPRESSED" in res.note and "fail-closed" in res.note


def test_anchored_config_with_anchor_terms_does_not_suppress(tmp_path):
    # The suppression fires ONLY on the empty-terms case.
    cfg = _AnchoredCfg()
    cfg.anchor_terms = ["dpd", "pvd"]
    res = stats_run.meta_analyze([_study_values("A", "CAR-T", {"pfs_hr": "0.50 (0.30-0.80)"})],
                                 {}, tmp_path, cfg=cfg)
    assert "SUPPRESSED" not in res.note


def test_meta_analyze_without_config_has_no_domain_defaults(tmp_path):
    res = stats_run.meta_analyze([_study("A", "Drug", "84%", "100")], {}, tmp_path)
    assert res.available is False
    assert "No configured poolable outcomes" in res.note


def test_meta_analyze_graceful_when_meta_absent_for_binary(monkeypatch, tmp_path):
    class Cfg:
        subgroup_field = ""
        subgroup_map = None
        dose_outcome = ""
        synthesis_arms = []

        def poolable_fields(self):
            return {"proportion": [], "log_effect": []}

        def binary_2x2_fields(self):
            return [{"field": "response_2x2", "label": "Response",
                     "pair": {"events_tx": "tx_events", "n_tx": "tx_n",
                              "events_ctrl": "ctrl_events", "n_ctrl": "ctrl_n"},
                     "beneficial": True}]

        def continuous_fields(self):
            return []

    monkeypatch.setattr(stats_run, "r_available", lambda: True)
    monkeypatch.setattr(stats_run, "metabin_available", lambda: False)
    monkeypatch.setattr(stats_run, "metacont_available", lambda: False)
    ext = [_study_values("A", "Drug", {"tx_events": "30", "tx_n": "100",
                                       "ctrl_events": "20", "ctrl_n": "100"})]
    res = stats_run.meta_analyze(ext, {}, tmp_path, cfg=Cfg())
    assert res.available is False
    assert "R/meta" in res.note
