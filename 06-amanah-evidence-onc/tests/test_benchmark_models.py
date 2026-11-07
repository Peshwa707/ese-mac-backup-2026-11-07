from __future__ import annotations

import pytest
from pydantic import ValidationError

from benchmark.models import (
    AnswerKey,
    BenchmarkCase,
    CandidateReview,
    OutcomeTarget,
    ReviewCitation,
    SamplingFrame,
    ScoreReport,
)


def _citation():
    return ReviewCitation(title="Published oncology meta-analysis", journal="JCO", year=2025)


def test_benchmark_models_round_trip():
    case = BenchmarkCase(
        case_id="onc-001",
        citation=_citation(),
        vertical_name="onc-001",
        intended_run_dir="runs/onc-001",
        answer_key_path="benchmark/keys/onc-001.json",
    )
    key = AnswerKey(
        case_id=case.case_id,
        expected_included_pmids=["1", "2"],
        expected_included_trial_ids=["trial-a"],
        expected_conclusion="uncertain",
        outcomes=[
            OutcomeTarget(
                field="orr",
                label="ORR",
                family="proportion",
                estimate=84.0,
                ci_lb=80.0,
                ci_ub=88.0,
                i2=0.0,
            )
        ],
    )
    frame = SamplingFrame(candidates=[CandidateReview(candidate_id="c1", citation=_citation())])
    report = ScoreReport(case_id=case.case_id)

    assert BenchmarkCase.model_validate_json(case.model_dump_json()).case_id == "onc-001"
    saved_key = AnswerKey.model_validate_json(key.model_dump_json())
    assert saved_key.outcomes[0].metric == "prop"
    assert saved_key.outcomes[0].effect_measure == "proportion"
    assert saved_key.expected_included_trial_ids == ["trial-a"]
    assert saved_key.expected_conclusion == "uncertain"
    assert SamplingFrame.model_validate_json(frame.model_dump_json()).candidates[0].candidate_id == "c1"
    assert ScoreReport.model_validate_json(report.model_dump_json()).case_id == "onc-001"


def test_sealed_answer_key_requires_metadata_and_complete_targets():
    with pytest.raises(ValidationError, match="seal_digest"):
        AnswerKey(case_id="onc-001", status="sealed")
    with pytest.raises(ValidationError, match="unfilled outcome targets"):
        AnswerKey(
            case_id="onc-001",
            status="sealed",
            seal_digest="sha256:abc",
            seal_signature="reviewer",
            outcomes=[OutcomeTarget(field="orr", family="proportion")],
        )


def test_rejects_malformed_ci_and_unknown_family():
    with pytest.raises(ValidationError, match="ci_lb"):
        OutcomeTarget(field="orr", family="proportion", estimate=80, ci_lb=90, ci_ub=70)
    with pytest.raises(ValidationError):
        OutcomeTarget(field="orr", family="magic", estimate=80)
    with pytest.raises(ValidationError):
        AnswerKey(case_id="onc-001", expected_included_trial_ids=["trial-a", "trial-a"])


def test_excluded_candidate_requires_reason():
    with pytest.raises(ValidationError, match="reason"):
        CandidateReview(candidate_id="c1", citation=_citation(), status="exclude")
