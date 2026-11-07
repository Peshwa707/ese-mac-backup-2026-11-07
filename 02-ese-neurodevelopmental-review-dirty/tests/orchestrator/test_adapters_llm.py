"""WP-M1.4b — LLM/async stage adapters (search, prescreen, screen, fulltext, extract).

The underlying network/LLM functions are stubbed; these tests verify the adapter
plumbing (load inputs -> call -> write hashed artifact -> summarize), not the
upstream functions (which have their own tests).
"""
import json

from core.models import Citation, IdType, RetrievalResult
from core.sr.agent_io import dump_corpus
from core.sr.orchestrator import adapters as ad
from core.sr.orchestrator.stages import RunContext
from core.sr.pipeline import Corpus
from core.sr.models import ExtractedStudy, ScreeningResult, ScreenDecision


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
                  seed_verification={}, per_source={"pubmed": len(cites)},
                  sources_executed=["pubmed"])


def _write_corpus(run_dir, cfg, cites):
    dump_corpus(_corpus(cfg, cites), run_dir / "corpus.json")


def _write_screening(run_dir, cites):
    screening = [ScreeningResult(pmid=c.id, decision=ScreenDecision.INCLUDE, reason="r",
                                 confidence=0.9) for c in cites]
    (run_dir / "screening.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in screening]), encoding="utf-8")


def _ctx(cfg, run_dir):
    return RunContext(cfg=cfg, run_dir=run_dir)


def test_search_adapter_writes_corpus(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()

    async def _stub(cfg_arg, *a, **k):
        return _corpus(cfg, cites)

    monkeypatch.setattr("core.sr.pipeline.gather_corpus", _stub)
    res = ad.search_adapter(tmp_path, _ctx(cfg, tmp_path))
    assert (tmp_path / "corpus.json").exists()
    assert res.summary["verified"] == 2


def test_prescreen_adapter_partitions_and_records_input(tmp_path):
    cfg = _cfg()
    _write_corpus(tmp_path, cfg, _cites())
    res = ad.prescreen_adapter(tmp_path, _ctx(cfg, tmp_path))
    assert (tmp_path / "prescreen.json").exists() and (tmp_path / "to_screen.json").exists()
    assert "counts" in res.summary
    assert len(res.deterministic_inputs["corpus.json"]) == 64


def test_screen_adapter_runs_and_summarizes_decisions(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    ad._dump_citations(tmp_path, "to_screen.json", cites)

    async def _stub(records, cfg_arg):
        return [ScreeningResult(pmid=c.id, decision=ScreenDecision.INCLUDE, reason="r",
                                confidence=0.9) for c in records]

    monkeypatch.setattr("core.sr.pipeline._screen_all", _stub)
    res = ad.screen_adapter(tmp_path, _ctx(cfg, tmp_path))
    assert (tmp_path / "screening.json").exists()
    assert res.summary["n"] == 2
    assert "to_screen.json" in res.deterministic_inputs


def test_extract_adapter_extracts_included(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _write_corpus(tmp_path, cfg, cites)
    _write_screening(tmp_path, cites)

    async def _stub(included, cfg_arg):
        return [ExtractedStudy(pmid=c.id, study_name=f"T{c.id}", values=[]) for c in included]

    monkeypatch.setattr("core.sr.pipeline._extract_all", _stub)
    res = ad.extract_adapter(tmp_path, _ctx(cfg, tmp_path))
    assert (tmp_path / "extractions.json").exists()
    assert res.summary["n"] == 2
    assert set(res.deterministic_inputs) == {"corpus.json", "screening.json"}


def test_fulltext_adapter_resolves_included(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _write_corpus(tmp_path, cfg, cites)
    _write_screening(tmp_path, cites)

    async def _stub(pmids, *a, **k):
        return []

    monkeypatch.setattr("core.sr.fulltext.resolve_many", _stub)
    res = ad.fulltext_adapter(tmp_path, _ctx(cfg, tmp_path))
    assert (tmp_path / "fulltext_coverage.json").exists()
    assert "total" in res.summary
