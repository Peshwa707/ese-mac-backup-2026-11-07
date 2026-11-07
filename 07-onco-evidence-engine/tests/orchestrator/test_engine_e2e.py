"""WP-M1.7 — orchestrator lifecycle + registry + migration equivalence."""
import json

import pytest

from core.models import Citation, IdType, RetrievalResult
from core.sr.orchestrator import adapters, engine
from core.sr.orchestrator import gates
from core.sr.orchestrator import state as st
from core.sr.pipeline import Corpus
from core.sr.models import ExtractedStudy, ScreeningResult, ScreenDecision

TS = "2026-06-16T00:00:00Z"
KEY = b"k" * 32


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _cites():
    return [Citation(id_type=IdType.PMID, id=p, source="pubmed", title=f"T{p}",
                     snippet="abstract", verified=True, verification="live")
            for p in ("111", "222")]


def _corpus(cfg, cites):
    return Corpus(cfg=cfg, run_id="t",
                  search_result=RetrievalResult(source="pubmed", query="q", citations=cites),
                  identified=cites, deduped=cites, verified=cites, rejected=[],
                  seed_verification={}, per_source={"pubmed": 2}, sources_executed=["pubmed"])


def _stub_chain(monkeypatch, cfg, cites):
    corpus = _corpus(cfg, cites)

    async def _gc(c, *a, **k):
        return corpus

    async def _sa(records, c):
        return [ScreeningResult(pmid=r.id, decision=ScreenDecision.INCLUDE, reason="r",
                                confidence=0.9) for r in records]

    async def _ex(included, c):
        return [ExtractedStudy(pmid=r.id, study_name=f"T{r.id}", values=[]) for r in included]

    monkeypatch.setattr("core.sr.pipeline.gather_corpus", _gc)
    monkeypatch.setattr("core.sr.pipeline._screen_all", _sa)
    monkeypatch.setattr("core.sr.pipeline._extract_all", _ex)
    monkeypatch.setattr("core.sr.sentinel.check_named_trials",
                        lambda *a, **k: {"passed": True, "violations": [], "matched": {}})
    return corpus


def test_run_to_assemble_matches_build_run(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    corpus = _stub_chain(monkeypatch, cfg, cites)
    rd = engine.run_to(cfg, "assemble", created_utc=TS, runs_dir=tmp_path)
    srrun = json.loads((rd / "srrun.json").read_text())

    # Direct legacy assembly with identical stubbed data.
    from core.sr.prescreen import prescreen_records
    from core.sr.pipeline import build_run
    _, report = prescreen_records(corpus.verified)
    screening = [ScreeningResult(pmid=c.id, decision=ScreenDecision.INCLUDE, reason="r",
                                 confidence=0.9) for c in corpus.verified]
    extractions = [ExtractedStudy(pmid=c.id, study_name=f"T{c.id}", values=[]) for c in corpus.verified]
    expected = build_run(corpus, screening, extractions, prescreen=report)

    assert srrun["counts"]["included"] == expected.counts.included
    assert srrun["counts"]["screened"] == expected.counts.screened
    assert srrun["counts"]["studies_included"] == expected.counts.studies_included


def test_auto_leaves_honesty_gates_unapproved_and_export_blocked(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _stub_chain(monkeypatch, cfg, cites)
    rd = engine.run_to(cfg, "assemble", created_utc=TS, runs_dir=tmp_path)
    state = st.load(rd)
    # honesty-gate stages ran (done) but are NOT approved (no human in --auto)
    assert state.stages["screen"].status == "done"
    assert state.stages["extract"].status == "done"
    ok, reasons = gates.export_allowed(rd, state, engine.build_registry(), key=KEY)
    assert ok is False                       # auto draft is not exportable
    assert any("screen" in r or "extract" in r for r in reasons)


def test_approve_upgrades_gate_to_approved(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _stub_chain(monkeypatch, cfg, cites)
    rd = engine.run_to(cfg, "assemble", created_utc=TS, runs_dir=tmp_path)
    engine.approve(rd, "extract", "dr.smith", ts_utc=TS, key=KEY)
    state = st.load(rd)
    assert state.stages["extract"].status == "approved"
    assert gates.load_record(rd, "extract") is not None


def test_resume_continues_from_partial(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _stub_chain(monkeypatch, cfg, cites)
    rd = engine.create_run(cfg, created_utc=TS, auto=True, runs_dir=tmp_path)
    # run only the first stage, then resume the rest
    engine.run_stage(rd, [s for s in engine.build_registry() if s.name == "search"][0], cfg)
    assert (rd / "corpus.json").exists() and not (rd / "srrun.json").exists()
    engine.resume(rd, cfg, "assemble")
    assert (rd / "srrun.json").exists()


def test_upstream_artifact_edit_restales_downstream(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _stub_chain(monkeypatch, cfg, cites)
    rd = engine.run_to(cfg, "assemble", created_utc=TS, runs_dir=tmp_path)
    # edit corpus.json on disk after the run (bytes change -> hash change)
    (rd / "corpus.json").write_text((rd / "corpus.json").read_text() + "\n")
    state = st.load(rd)
    changed = engine._refresh_staleness(rd, state, engine.build_registry(cfg))
    assert changed
    state = st.load(rd)
    assert state.stages["prescreen"].status == "stale"      # consumed the edited corpus
    assert state.stages["assemble"].status == "stale"       # true descendant restaled


def test_orchestrated_run_emits_provenance_bundle(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _stub_chain(monkeypatch, cfg, cites)
    rd = engine.run_to(cfg, "assemble", created_utc=TS, runs_dir=tmp_path)
    for f in ("search_manifest.json", "evidence_strength.json", "environment.json", "provenance.json"):
        assert (rd / f).exists(), f"missing {f}"


def test_run_stage_propagates_failure_closed(tmp_path, monkeypatch):
    cfg = _cfg()
    rd = engine.create_run(cfg, created_utc=TS, runs_dir=tmp_path)

    def _boom(run_dir, ctx):
        raise RuntimeError("kaboom-secret-sk-12345")

    monkeypatch.setattr(adapters, "search_adapter", _boom)
    stage = [s for s in engine.build_registry() if s.name == "search"][0]
    with pytest.raises(RuntimeError):
        engine.run_stage(rd, stage, cfg)
    ss = st.load(rd).stages["search"]
    assert ss.status == "failed"
    assert ss.error_code == "RuntimeError"          # type only, no raw message persisted
    assert "secret" not in json.dumps(ss.model_dump())


def test_runs_dir_gitignored():
    from pathlib import Path
    gi = Path(__file__).resolve().parents[2] / ".gitignore"
    assert "runs/" in gi.read_text()
