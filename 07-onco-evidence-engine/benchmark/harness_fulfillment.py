"""Offline helpers for inspecting harness fulfillment folders."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.models import HarnessStageStatus, HarnessStatusReport
from core.tools import harness


_STAGES = ("screen", "extract")


def _input_dir(hdir: Path, stage: str) -> Path:
    spec_stage = "screening" if stage == "screen" else "extraction"
    return hdir / f"{spec_stage}_in"


def _batch_files(hdir: Path, stage: str) -> list[Path]:
    return sorted(_input_dir(hdir, stage).glob("batch_*.json"))


def _expected_ids(batch_files: list[Path]) -> list[str]:
    ids: list[str] = []
    for path in batch_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("pmid") is not None:
                ids.append(str(item["pmid"]))
    return ids


def stage_status(run_dir: Path, stage: str, source: str) -> HarnessStageStatus:
    hdir = harness.harness_dir(run_dir, stage, source)
    if not hdir.exists():
        return HarnessStageStatus(stage=stage, source=source, status="missing")
    request = hdir / "REQUEST.md"
    _, spec = harness.tool_stage(stage)
    batch_files = _batch_files(hdir, stage)
    expected_outputs = harness.expected_result_names(spec, len(batch_files))
    missing = harness.missing_result_files(run_dir, stage, source, expected_outputs)
    if not expected_outputs or len(missing) == len(expected_outputs):
        status = "pending"
        valid_items = 0
        repair_items: list[str] = []
    elif missing:
        status = "partial"
        valid_items = 0
        repair_items = missing
    else:
        collect = harness.collect_results(run_dir, stage, source, _expected_ids(batch_files))
        status = "complete" if collect.ok else "partial"
        valid_items = len(collect.valid)
        repair_items = list(collect.missing)
    return HarnessStageStatus(
        stage=stage,
        source=source,
        request_path=str(request) if request.exists() else "",
        n_batches=len(batch_files),
        expected_outputs=expected_outputs,
        missing_outputs=missing,
        valid_items=valid_items,
        repair_items=repair_items,
        status=status,
    )


def status_report(run_dir: Path, source: str) -> HarnessStatusReport:
    return HarnessStatusReport(
        run_dir=str(run_dir),
        source=source,
        stages=[stage_status(run_dir, stage, source) for stage in _STAGES],
    )
