"""WP-M2.3 — dual independent-LLM screening + honest agreement."""
import json

import pytest

from core.models import Citation, IdType
from core.sr.orchestrator import adapters, dual_screen, engine
from core.sr.orchestrator.stages import RunContext
from core.sr.models import ScreenDecision, ScreeningBatch, ScreeningResult


def _cfg(reviewers=None):
    from core.sr.config import load_sr_config
    cfg = load_sr_config("mm-bcma-early-relapse")
    if reviewers is not None:
        object.__setattr__(cfg, "_reviewers_override", reviewers) if False else None
    return cfg


def _cites():
    return [Citation(id_type=IdType.PMID, id=p, source="pubmed", title=f"T{p}",
                     snippet="abstract", verified=True, verification="live")
            for p in ("111", "222")]


def _stub_gateway(monkeypatch):
    """R1 includes both; R2 includes 111 but excludes 222 -> one agreement, one conflict."""
    def _fake(schema, system, user, *, stage, spec, max_tokens=8000):
        if "claude" in spec.model:          # R1
            results = [ScreeningResult(pmid=p, decision=ScreenDecision.INCLUDE, reason="r1",
                                       confidence=0.9) for p in ("111", "222")]
        else:                               # R2 (local)
            results = [ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE, reason="r2", confidence=0.8),
                       ScreeningResult(pmid="222", decision=ScreenDecision.EXCLUDE, reason="r2", confidence=0.7)]
        return ScreeningBatch(results=results)
    monkeypatch.setattr("core.llm.gateway.gateway_parse", _fake)


def test_dual_screen_writes_all_artifacts_and_honest_metric(tmp_path, monkeypatch):
    _stub_gateway(monkeypatch)
    adapters._dump_citations(tmp_path, "to_screen.json", _cites())
    res = dual_screen.dual_screen_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    for art in ("screening.json", "screening_r1.json", "screening_r2.json", "reconcile.json"):
        assert (tmp_path / art).exists()
    # metric is honestly labelled, never "inter-rater reliability"
    assert "llm_llm_agreement" in res.summary
    rec = json.loads((tmp_path / "reconcile.json").read_text())
    assert rec["metric"] == "llm_llm_agreement"
    assert "not inter-rater reliability" in rec["metric_note"].lower()   # honest disclaimer present


def test_dual_screen_uses_distinct_reviewer_stage_tags(tmp_path, monkeypatch):
    # dual_screen must tag each reviewer's calls with a distinct stage (screening_r1/r2)
    # so the gateway's stage_log/provenance can audit which model was R1 vs R2.
    calls = []

    def _fake(schema, system, user, *, stage, spec, max_tokens=8000):
        calls.append((stage, spec.model))
        return ScreeningBatch(results=[
            ScreeningResult(pmid=p, decision=ScreenDecision.INCLUDE, reason="x", confidence=0.9)
            for p in ("111", "222")])

    monkeypatch.setattr("core.llm.gateway.gateway_parse", _fake)
    adapters._dump_citations(tmp_path, "to_screen.json", _cites())
    dual_screen.dual_screen_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    by_stage = dict(calls)
    assert {"screening_r1", "screening_r2"} <= set(by_stage)
    assert by_stage["screening_r1"] != by_stage["screening_r2"]   # distinct models per reviewer


def test_disagreement_becomes_uncertain_conflict(tmp_path, monkeypatch):
    _stub_gateway(monkeypatch)
    adapters._dump_citations(tmp_path, "to_screen.json", _cites())
    res = dual_screen.dual_screen_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    consensus = {r["pmid"]: r["decision"] for r in json.loads((tmp_path / "screening.json").read_text())}
    assert consensus["111"] == ScreenDecision.INCLUDE.value      # agreement passes through
    assert consensus["222"] == ScreenDecision.UNCERTAIN.value    # conflict -> UNCERTAIN (human adjudicates)
    assert res.summary["n_conflicts"] == 1


def test_non_independent_reviewers_fail_closed(tmp_path, monkeypatch):
    _stub_gateway(monkeypatch)
    adapters._dump_citations(tmp_path, "to_screen.json", _cites())

    class _SameFamilyCfg:
        reviewers = {"r1": {"source": "anthropic", "model": "claude-opus-4-8"},
                     "r2": {"source": "anthropic", "model": "claude-sonnet-4-6"}}
        # screening prompt needs the real cfg fields; borrow them
        def __getattr__(self, k):
            from core.sr.config import load_sr_config
            return getattr(load_sr_config("mm-bcma-early-relapse"), k)

    from core.sr.orchestrator.reviewers import ReviewerIndependenceError
    with pytest.raises(ReviewerIndependenceError):
        dual_screen.dual_screen_adapter(tmp_path, RunContext(cfg=_SameFamilyCfg(), run_dir=tmp_path))


def test_registry_selects_dual_screen_when_roster_configured():
    class _Cfg:
        reviewers = {"r1": {"source": "anthropic", "model": "claude-opus-4-8"},
                     "r2": {"source": "local", "model": "llama3.1:70b", "endpoint": "http://x"}}
    screen = [s for s in engine.build_registry(_Cfg()) if s.name == "screen"][0]
    assert screen.fn is dual_screen.dual_screen_adapter
    # no roster -> single-reviewer screen
    screen0 = [s for s in engine.build_registry(None) if s.name == "screen"][0]
    assert screen0.fn is adapters.screen_adapter
