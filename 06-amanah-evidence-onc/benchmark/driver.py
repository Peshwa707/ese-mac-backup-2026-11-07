"""Dry-run benchmark driver planning."""

from __future__ import annotations

from pathlib import Path

from benchmark.answer_key import load_case
from benchmark.models import RunPlan


EXPECTED_FULL_REVIEW_ARTIFACTS = [
    "corpus.json",
    "records_for_agents.json",
    "screening_merged.json",
    "includes.json",
    "extractions.json",
    "fulltext_report.json",
    "rob.json",
    "grade.json",
    "stats_configured.json",
    "narrative.json",
    "narrative_final.json",
    "manuscript.json",
]


def create_run_plan(case_path: Path, out_path: Path, *, harness_source: str = "claude") -> RunPlan:
    case = load_case(case_path)
    commands: list[str] = []
    caveats: list[str] = [
        "Plan only: Phase 0 does not compile protocols, query PubMed, call AI, or run the engine.",
        "Use EVIDENCE_POSTURE=harness rather than --harness so unsupported AI tools remain cloud-routed later.",
    ]
    if case.protocol_spec_path:
        commands.append(
            "python -m core.sr.protocol "
            f"--spec {case.protocol_spec_path} --name {case.vertical_name}"
        )
    elif case.protocol_source_path:
        commands.append(
            "python -m core.sr.protocol "
            f"--draft {case.protocol_source_path} --name {case.vertical_name}"
        )
        caveats.append("Draft protocol compilation uses the native LLM path in a later phase.")
    else:
        caveats.append("No protocol source/spec path recorded on the benchmark case.")
    commands.append(
        "EVIDENCE_POSTURE=harness python -m core.tools recipe run full-review "
        f"{case.intended_run_dir} --vertical {case.vertical_name} --harness-source {harness_source}"
    )
    plan = RunPlan(
        case_id=case.case_id,
        commands=commands,
        expected_artifacts=EXPECTED_FULL_REVIEW_ARTIFACTS,
        caveats=caveats,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return plan
