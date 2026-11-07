"""Phase 1b: runtime per-stage model provenance + selective-extractability flags."""

from __future__ import annotations

from dataclasses import dataclass

from core.llm import client
from core.sr.ai_reporting import build_ai_reporting
from core.sr.review_flags import selective_extraction_flags


class _Profile:
    label = "Systematic review"
    reporting = "PRISMA 2020"


class _Run:
    provenance_id = "deadbeefcafebabe"
    reviewer_signoff = None


def test_stage_log_records_model_and_backend(monkeypatch):
    client.reset_stage_log()
    # Don't hit the network: stub the SDK call, exercise the recorder in parse().
    monkeypatch.setattr(client, "_get_client", lambda: (_ for _ in ()).throw(AssertionError))
    client._record_stage("screening", "qwen3.5", "ollama")
    client._record_stage("screening", "qwen3.5", "ollama")
    client._record_stage("synthesis", "claude-opus-4-8", "anthropic")
    log = client.stage_log()
    screening = next(e for e in log if e["stage"] == "screening")
    assert screening["n_calls"] == 2 and screening["backend"] == "ollama"
    assert any(e["backend"] == "anthropic" for e in log)


def test_ai_reporting_uses_runtime_models_and_flags_local(monkeypatch):
    stage_models = [
        {"stage": "screening", "model": "qwen3.5", "backend": "ollama", "n_calls": 40},
        {"stage": "synthesis", "model": "claude-opus-4-8", "backend": "anthropic", "n_calls": 1},
    ]
    rep = build_ai_reporting(_Run(), None, _Profile(), "2026-06-08", stage_models=stage_models)
    screening = next(s for s in rep["stages"] if s["tag"] == "screening")
    assert "qwen3.5 (ollama" in screening["model"]            # runtime model, not hardcoded
    assert "Sonnet" not in screening["model"]
    # A local/cheaper backend triggers the elevated-review caveat.
    assert "elevated human-review" in rep["human_oversight"]
    # Hard lock with no sign-off is reported in the statement.
    assert "No human sign-off" in rep["human_oversight"]


def test_ai_reporting_discloses_harness_without_local_caveat():
    stage_models = [
        {"stage": "screening", "model": "GPT (ChatGPT Pro / Codex)",
         "backend": "harness", "n_calls": 1},
    ]
    rep = build_ai_reporting(_Run(), None, _Profile(), "2026-06-08", stage_models=stage_models)
    screening = next(s for s in rep["stages"] if s["tag"] == "screening")
    assert "GPT (ChatGPT Pro / Codex) (harness" in screening["model"]
    assert "elevated human-review" not in rep["human_oversight"]


@dataclass
class _Outcome:
    label: str
    n_pooled: int


@dataclass
class _Stats:
    available: bool
    outcomes: list
    hr_outcomes: list


def test_selective_extraction_flags_fire_below_threshold():
    stats = _Stats(True, [_Outcome("ORR", 2), _Outcome("CRS", 9)], [])
    flags = selective_extraction_flags(stats, n_included=10, threshold=0.5)
    # ORR (2/10) flagged; CRS (9/10) not.
    assert len(flags) == 1 and "ORR" in flags[0] and "2/10" in flags[0]


def test_no_flags_when_stats_unavailable_or_complete():
    assert selective_extraction_flags(_Stats(False, [], []), 10) == []
    assert selective_extraction_flags(_Stats(True, [_Outcome("ORR", 10)], []), 10) == []
