from __future__ import annotations

import json

from benchmark.harness_fulfillment import stage_status, status_report
from core.tools import harness


def _screen_harness(run_dir, source="gpt", n_batches=1):
    hdir = harness.harness_dir(run_dir, "screen", source)
    in_dir = hdir / "screening_in"
    in_dir.mkdir(parents=True)
    for idx in range(n_batches):
        start = idx * 2 + 1
        (in_dir / f"batch_{idx:03d}.json").write_text(json.dumps([
            {"pmid": str(start), "title": f"T{start}", "abstract": "A"},
            {"pmid": str(start + 1), "title": f"T{start + 1}", "abstract": "A"},
        ]), encoding="utf-8")
    (hdir / "REQUEST.md").write_text("request", encoding="utf-8")
    return hdir


def test_harness_status_pending_partial_and_complete(tmp_path):
    hdir = _screen_harness(tmp_path, n_batches=2)

    pending = stage_status(tmp_path, "screen", "gpt")
    assert pending.status == "pending"
    assert pending.missing_outputs == ["screen_batch_000.json", "screen_batch_001.json"]

    (hdir / "screen_batch_000.json").write_text(json.dumps([
        {"pmid": "1", "decision": "include", "reason": "eligible", "confidence": 0.9},
        {"pmid": "2", "decision": "exclude", "reason": "wrong design", "confidence": 0.8},
    ]), encoding="utf-8")
    partial = stage_status(tmp_path, "screen", "gpt")
    assert partial.status == "partial"
    assert partial.missing_outputs == ["screen_batch_001.json"]

    (hdir / "screen_batch_001.json").write_text(json.dumps([
        {"pmid": "3", "decision": "include", "reason": "eligible", "confidence": 0.9},
        {"pmid": "4", "decision": "exclude", "reason": "wrong design", "confidence": 0.8},
    ]), encoding="utf-8")
    complete = stage_status(tmp_path, "screen", "gpt")
    assert complete.status == "complete"
    assert complete.valid_items == 4

    report = status_report(tmp_path, "gpt")
    assert [s.stage for s in report.stages] == ["screen", "extract"]


def test_screen_harness_guardrails_drop_hallucinated_and_mark_omitted_uncertain(tmp_path):
    hdir = _screen_harness(tmp_path, n_batches=1)
    (hdir / "screen_batch_000.json").write_text(json.dumps([
        {"pmid": "1", "decision": "include", "reason": "eligible", "confidence": 0.9},
        {"pmid": "999", "decision": "include", "reason": "hallucinated", "confidence": 0.9},
    ]), encoding="utf-8")

    collected = harness.collect_results(tmp_path, "screen", "gpt", ["1", "2"])
    by_pmid = {r.pmid: r for r in collected.valid}

    assert set(by_pmid) == {"1", "2"}
    assert by_pmid["2"].decision.value == "uncertain"
    assert "omitted" in by_pmid["2"].reason.lower()
    assert collected.missing == []


def test_missing_harness_directory_reports_missing(tmp_path):
    status = stage_status(tmp_path, "extract", "claude")
    assert status.status == "missing"
