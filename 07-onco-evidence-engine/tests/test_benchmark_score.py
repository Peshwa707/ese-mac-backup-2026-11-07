from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmark.models import AnswerKey, OutcomeTarget, ScoreReport
from benchmark.report import aggregate_scores
from benchmark.score import score_run


def _write_run_folder(run_dir):
    run_dir.mkdir()
    (run_dir / "includes.json").write_text(json.dumps(["1", "3"]), encoding="utf-8")
    (run_dir / "screening_merged.json").write_text(json.dumps([
        {"pmid": "1", "decision": "include", "reason": "eligible", "confidence": 0.9},
        {"pmid": "3", "decision": "include", "reason": "eligible", "confidence": 0.8},
    ]), encoding="utf-8")
    (run_dir / "extractions.json").write_text(json.dumps([
        {
            "pmid": "1",
            "trial_id": "trial-a",
            "member_pmids": ["1", "2"],
            "values": [
                {"field": "sample_size", "group": "population", "value": "ESTIMATED: 100", "flag": "ESTIMATED"},
                {"field": "prior_lines", "group": "population", "value": "NR", "flag": "NR"},
            ],
        },
        {"pmid": "3", "trial_id": "trial-b", "member_pmids": [], "values": []},
    ]), encoding="utf-8")
    (run_dir / "stats_configured.json").write_text(json.dumps({
        "available": True,
        "note": "",
        "outcomes": [{
            "field": "orr",
            "label": "Overall response",
            "n_pooled": 2,
            "n_excluded": 1,
            "forest_png": "",
            "results": [{"group": "ALL", "k": 2, "prop": 84.0, "ci_lb": 80.0, "ci_ub": 88.0, "I2": 0}],
        }],
        "hr_outcomes": [{
            "field": "pfs_hr",
            "label": "PFS HR",
            "n_pooled": 2,
            "n_excluded": 0,
            "forest_png": "",
            "results": [{"group": "ALL", "k": 2, "hr": 0.71, "ci_lb": 0.60, "ci_ub": 0.84, "I2": 0}],
        }],
        "bin_outcomes": [{
            "field": "tox_rr",
            "label": "Toxicity RR",
            "n_pooled": 2,
            "n_excluded": 0,
            "forest_png": "",
            "results": [{
                "group": "ALL", "k": 2, "measure": "RR", "estimate": 1.2,
                "ci_lb": 1.0, "ci_ub": 1.4, "rd": 0.1, "number_needed": {"label": "NNC"}, "I2": 0,
            }],
        }],
        "cont_outcomes": [{
            "field": "qol_smd",
            "label": "QoL SMD",
            "n_pooled": 2,
            "n_excluded": 0,
            "forest_png": "",
            "results": [{"group": "ALL", "k": 2, "measure": "SMD", "estimate": 0.3, "ci_lb": 0.1, "ci_ub": 0.5, "I2": 0}],
        }],
    }), encoding="utf-8")


def test_score_run_requires_sealed_key_by_default(tmp_path):
    run_dir = tmp_path / "run"
    _write_run_folder(run_dir)
    key_path = tmp_path / "answer_key.json"
    key_path.write_text(AnswerKey(case_id="onc-001").model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="allow-draft"):
        score_run(run_dir, key_path, tmp_path / "score.json")


def test_score_run_compares_all_outcome_families_and_selection(tmp_path):
    run_dir = tmp_path / "run"
    _write_run_folder(run_dir)
    key_path = tmp_path / "answer_key.json"
    key_path.write_text(AnswerKey(
        case_id="onc-001",
        expected_included_pmids=["1", "2"],
        expected_included_trial_ids=["trial-a"],
        expected_conclusion="effect",
        outcomes=[
            OutcomeTarget(field="orr", label="Overall response", family="proportion", estimate=84.0, ci_lb=80.0, ci_ub=88.0, i2=0),
            OutcomeTarget(field="pfs_hr", family="log_effect", effect_measure="hr", estimate=0.70, ci_lb=0.60, ci_ub=0.84, i2=5),
            OutcomeTarget(field="tox_rr", family="binary", effect_measure="rr", estimate=1.2, ci_lb=0.9, ci_ub=1.4),
            OutcomeTarget(field="qol_smd", family="continuous", effect_measure="smd", estimate=-0.2, ci_lb=-0.4, ci_ub=-0.1),
            OutcomeTarget(field="missing", family="proportion", estimate=50.0),
            OutcomeTarget(field="unfilled", family="proportion"),
        ],
    ).model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "narrative.json").write_text(json.dumps({
        "locked": False,
        "qualitative_stance": "effect",
        "final": {"abstract_conclusions": "The intervention improved outcomes."},
    }), encoding="utf-8")
    out = tmp_path / "score.json"

    report = score_run(run_dir, key_path, out, allow_draft=True)
    by_field = {s.field: s for s in report.outcomes}

    assert report.selection.expected_n == 2
    assert report.selection.engine_n == 2
    assert report.selection.overlap_n == 1
    assert round(report.selection.recall, 2) == 0.5
    assert report.selection.study_expected_n == 1
    assert report.selection.study_engine_n == 2
    assert report.selection.study_overlap_n == 1
    assert report.integrity.estimated_value_count == 1
    assert report.integrity.nr_value_count == 1
    assert report.integrity.pooled_n_excluded_total == 1
    assert report.conclusion.matched is True
    assert by_field["orr"].classification == "matched"
    assert by_field["orr"].expected_direction == "na"
    assert by_field["orr"].observed_significance == "na"
    assert by_field["orr"].headline_pass is True
    assert by_field["pfs_hr"].classification == "matched"
    assert by_field["pfs_hr"].null_value == 1.0
    assert by_field["pfs_hr"].direction_agree is True
    assert by_field["pfs_hr"].significance_agree is True
    assert by_field["pfs_hr"].ci_interval_overlap is True
    assert by_field["pfs_hr"].i2_delta == -5
    assert by_field["tox_rr"].classification == "matched"
    assert by_field["tox_rr"].expected_significance == "not_significant"
    assert by_field["qol_smd"].classification == "mismatched"
    assert by_field["qol_smd"].null_value == 0.0
    assert by_field["qol_smd"].direction_agree is False
    assert by_field["qol_smd"].headline_pass is False
    assert by_field["missing"].classification == "missing_engine"
    assert by_field["unfilled"].classification == "not_scored"
    assert report.key_sealed is False
    assert any("DRAFT ANSWER KEY USED" in c for c in report.caveats)
    assert ScoreReport.model_validate_json(out.read_text(encoding="utf-8")).case_id == "onc-001"


def test_score_run_reports_missing_key_for_unexpected_engine_outcome(tmp_path):
    run_dir = tmp_path / "run"
    _write_run_folder(run_dir)
    key = tmp_path / "answer_key.json"
    key.write_text(AnswerKey(case_id="onc-001").model_dump_json(indent=2), encoding="utf-8")

    report = score_run(run_dir, key, tmp_path / "score.json", allow_draft=True)

    assert report.counts_by_classification["missing_key"] == 4


def test_score_run_reads_locked_narrative_as_withheld(tmp_path):
    run_dir = tmp_path / "run"
    _write_run_folder(run_dir)
    (run_dir / "narrative.json").write_text(json.dumps({
        "locked": True,
        "final": {"abstract_conclusions": "CONCLUSIONS LOCKED - withheld."},
    }), encoding="utf-8")
    key = tmp_path / "answer_key.json"
    key.write_text(AnswerKey(case_id="onc-001", expected_conclusion="withheld").model_dump_json(indent=2), encoding="utf-8")

    report = score_run(run_dir, key, tmp_path / "score.json", allow_draft=True)

    assert report.conclusion.observed == "withheld"
    assert report.conclusion.matched is True


def test_aggregate_report_keeps_classification_counts_and_caveats(tmp_path):
    run_dir = tmp_path / "run"
    _write_run_folder(run_dir)
    key = tmp_path / "answer_key.json"
    key.write_text(AnswerKey(
        case_id="onc-001",
        caveats=["answer key caveat"],
        outcomes=[OutcomeTarget(field="orr", family="proportion", estimate=84.0, ci_lb=80.0, ci_ub=88.0)],
    ).model_dump_json(indent=2), encoding="utf-8")
    score_path = tmp_path / "score.json"
    score_run(run_dir, key, score_path, allow_draft=True)

    summary = aggregate_scores([score_path], tmp_path / "summary.json", tmp_path / "summary.md")

    assert summary["n_cases"] == 1
    assert summary["outcome_counts"]["matched"] == 1
    assert summary["headline_counts"]["pass"] == 1
    assert summary["key_sealed_counts"]["draft"] == 1
    assert summary["integrity_totals"]["estimated_value_count"] == 1
    assert "answer key caveat" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_real_run_fixture_scores_real_artifacts():
    root = Path(__file__).parent / "fixtures" / "benchmark"
    report = score_run(
        root / "real_run_min",
        root / "real_run_answer_key.json",
        Path("/tmp/benchmark-real-fixture-score.json"),
        allow_draft=True,
    )

    by_field = {s.field: s for s in report.outcomes}
    assert report.selection.report_engine_n == 4
    assert report.selection.report_overlap_n == 4
    assert report.selection.study_engine_n == 4
    assert report.selection.study_overlap_n == 4
    assert report.integrity.estimated_value_count > 0
    assert report.integrity.nr_value_count > 0
    assert report.integrity.pooled_n_excluded_total == 0
    assert report.conclusion.observed == "withheld"
    assert report.conclusion.matched is True
    assert by_field["cr_or_better_rate"].classification == "matched"
    assert by_field["cr_or_better_rate"].headline_pass is True
    assert by_field["cr_or_better_rate"].expected_significance == "na"
