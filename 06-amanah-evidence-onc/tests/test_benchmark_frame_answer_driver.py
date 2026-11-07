from __future__ import annotations

import json

from benchmark.answer_key import draft_answer_key
from benchmark.driver import create_run_plan
from benchmark.frame import build_sampling_frame
from benchmark.models import AnswerKey, RunPlan, SamplingFrame


def _case_payload(tmp_path):
    return {
        "case_id": "onc-001",
        "citation": {"title": "Published oncology meta-analysis"},
        "vertical_name": "onc-001",
        "intended_run_dir": str(tmp_path / "run"),
        "answer_key_path": str(tmp_path / "answer_key.json"),
        "protocol_spec_path": str(tmp_path / "spec.json"),
        "outcome_slots": [
            {"field": "orr", "label": "Overall response", "family": "proportion"},
            {"field": "pfs_hr", "label": "PFS HR", "family": "log_effect"},
        ],
    }


def test_sampling_frame_build_is_offline_and_deterministic(tmp_path):
    src = tmp_path / "candidates.json"
    src.write_text(json.dumps([
        {"candidate_id": "b", "citation": {"title": "B"}},
        {"candidate_id": "a", "citation": {"title": "A"}, "status": "include", "reason": "eligible"},
    ]), encoding="utf-8")
    out = tmp_path / "sampling_frame.json"

    frame = build_sampling_frame(src, out)
    saved = SamplingFrame.model_validate_json(out.read_text(encoding="utf-8"))

    assert [c.candidate_id for c in frame.candidates] == ["a", "b"]
    assert [c.candidate_id for c in saved.candidates] == ["a", "b"]


def test_answer_key_draft_creates_empty_slots_and_invents_no_results(tmp_path):
    case = tmp_path / "case.json"
    case.write_text(json.dumps(_case_payload(tmp_path)), encoding="utf-8")
    out = tmp_path / "answer_key.json"

    key = draft_answer_key(case, out)
    saved = AnswerKey.model_validate_json(out.read_text(encoding="utf-8"))

    assert key.status == "draft"
    assert [o.field for o in saved.outcomes] == ["orr", "pfs_hr"]
    assert all(o.estimate is None for o in saved.outcomes)
    assert saved.expected_included_pmids == []
    assert "No engine outputs" in " ".join(saved.caveats)


def test_driver_plan_writes_commands_but_runs_nothing(tmp_path):
    case = tmp_path / "case.json"
    case.write_text(json.dumps(_case_payload(tmp_path)), encoding="utf-8")
    out = tmp_path / "run_plan.json"

    plan = create_run_plan(case, out, harness_source="gpt")
    saved = RunPlan.model_validate_json(out.read_text(encoding="utf-8"))

    assert plan.commands == saved.commands
    assert saved.commands[0].startswith("python -m core.sr.protocol --spec")
    assert "EVIDENCE_POSTURE=harness python -m core.tools recipe run full-review" in saved.commands[1]
    assert "--harness-source gpt" in saved.commands[1]
    assert "manuscript.json" in saved.expected_artifacts
    assert any("Phase 0 does not" in c for c in saved.caveats)
