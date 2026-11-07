"""Draft human-fillable answer-key scaffolds."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.models import AnswerKey, BenchmarkCase, OutcomeTarget


def load_case(path: Path) -> BenchmarkCase:
    return BenchmarkCase.model_validate_json(path.read_text(encoding="utf-8"))


def draft_answer_key(case_path: Path, out_path: Path) -> AnswerKey:
    case = load_case(case_path)
    outcomes = [
        OutcomeTarget(field=s.field, label=s.label, family=s.family, group=s.group)
        for s in case.outcome_slots
    ]
    caveats = [
        "Draft scaffold only: published results and included-study ids must be filled by a human/PAI pass.",
        "No engine outputs were read and no published-result values were inferred.",
    ]
    key = AnswerKey(case_id=case.case_id, status="draft", outcomes=outcomes, caveats=caveats)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(key.model_dump(mode="json"), indent=2), encoding="utf-8")
    return key
