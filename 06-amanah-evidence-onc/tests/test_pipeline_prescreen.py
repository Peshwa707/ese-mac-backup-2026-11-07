"""WP3 (re: rq-ca75fe0b966c) — the native run_sr/CLI screening path must apply
the publication-type pre-screen before LLM screening, not just the tool/API paths.
"""
import asyncio

from core.models import Citation, IdType, RetrievalResult
from core.sr.config import load_sr_config
from core.sr.pipeline import Corpus, run_sr
import core.sr.pipeline as P


def _corpus():
    cfg = load_sr_config("mm-bcma-3l")
    cites = [
        Citation(id_type=IdType.PMID, id="ed1", source="pubmed", title="An editorial on CAR-T",
                 snippet="opinion", study_type="Editorial", verified=True, verification="live"),
        Citation(id_type=IdType.PMID, id="rct1", source="pubmed", title="A phase 3 RCT",
                 snippet="trial", study_type="Randomized Controlled Trial",
                 verified=True, verification="live"),
    ]
    return cfg, Corpus(
        cfg=cfg, run_id="t", search_result=RetrievalResult(source="pubmed", query="q", citations=cites),
        identified=cites, deduped=cites, verified=cites, rejected=[], seed_verification={},
        per_source={"pubmed": 2}, sources_executed=["pubmed"],
    )


def test_run_sr_applies_prescreen_before_screening(monkeypatch):
    cfg, corpus = _corpus()
    captured = {}

    async def fake_gather(cfg, retmax=None, connector=None):
        return corpus

    async def fake_screen_all(cites_in, cfg):
        captured["ids"] = [c.id for c in cites_in]
        return []

    async def fake_extract_all(inc, cfg):
        return []

    monkeypatch.setattr(P, "gather_corpus", fake_gather)
    monkeypatch.setattr(P, "_screen_all", fake_screen_all)
    monkeypatch.setattr(P, "_extract_all", fake_extract_all)

    run = asyncio.run(run_sr(cfg, do_extract=False))

    assert "ed1" not in captured["ids"]          # editorial filtered out before LLM
    assert "rct1" in captured["ids"]             # trial reaches screening
    assert run.prescreen.get("exclude_by_type") == ["ed1"]   # report threaded into SRRun
    # PRISMA counts must NOT over-report screening volume:
    assert run.counts.screened == 1              # only rct1 actually screened (not 2)
    assert run.counts.prescreen_excluded == 1    # the editorial, accounted separately


def test_load_run_bundle_reads_prescreen_report(tmp_path):
    """Persisted run-folder rebuild must read prescreen.json so manuscript exports
    don't over-report screened records (re: rq-9d4e4a25c622)."""
    import json
    from core.sr.agent_io import dump_corpus, records_for_agents
    from core.sr.models import ScreeningResult, ScreenDecision
    from core.tools.sr_io import load_run_bundle

    cfg, corpus = _corpus()
    dump_corpus(corpus, tmp_path / "corpus.json")
    (tmp_path / "records_for_agents.json").write_text(json.dumps(records_for_agents(corpus)))
    # _run_screen would have written these:
    (tmp_path / "prescreen.json").write_text(json.dumps({
        "counts": {"screen": 1, "exclude_by_type": 1, "conference_abstract": 0},
        "exclude_by_type": ["ed1"], "conference_abstract": [],
    }))
    (tmp_path / "screening_merged.json").write_text(json.dumps([
        ScreeningResult(pmid="rct1", decision=ScreenDecision.INCLUDE,
                        reason="phase 3 RCT", confidence=0.9).model_dump(mode="json")
    ]))
    (tmp_path / "extractions.json").write_text("[]")
    bundle = load_run_bundle(tmp_path, vertical="mm-bcma-3l")
    assert bundle.run.counts.screened == 1            # not len(verified)=2
    assert bundle.run.counts.prescreen_excluded == 1
