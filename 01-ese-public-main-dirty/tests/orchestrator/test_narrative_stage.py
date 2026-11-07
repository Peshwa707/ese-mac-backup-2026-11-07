"""Narrative stage wiring — prose is drafted in the pipeline and flows to export.

The `narrative` stage reuses the registered narrative tool's grounded-brief → draft →
per-claim support-check path. Here the LLM is stubbed so the test stays offline; it
proves the stage produces narrative_final.json + narrative.json, that the stage is
wired into the DAG before `manuscript`, and that the drafted prose reaches the
assembled manuscript components (no TODO stubs).
"""
from __future__ import annotations

import json

from core.models import Citation, IdType, RetrievalResult
from core.sr.models import ExtractedStudy, ScreenDecision, ScreeningResult
from core.sr.orchestrator import adapters, engine
from core.sr.orchestrator.stages import RunContext, topo_order
from core.sr.pipeline import Corpus

TS = "2026-06-16T00:00:00Z"


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _cites():
    return [Citation(id_type=IdType.PMID, id=p, source="pubmed", title=f"T{p}",
                     snippet="In this phase 3 trial the ORR was 84%.",
                     verified=True, verification="live")
            for p in ("111", "222")]


def _stub_chain(monkeypatch, cfg, cites):
    corpus = Corpus(cfg=cfg, run_id="t",
                    search_result=RetrievalResult(source="pubmed", query="q", citations=cites),
                    identified=cites, deduped=cites, verified=cites, rejected=[],
                    seed_verification={}, per_source={"pubmed": 2}, sources_executed=["pubmed"])

    async def _gc(c, *a, **k):
        return corpus

    async def _sa(records, c):
        return [ScreeningResult(pmid=r.id, decision=ScreenDecision.INCLUDE, reason="r",
                                confidence=0.9, modality="CAR-T") for r in records]

    async def _ex(included, c):
        return [ExtractedStudy(pmid=r.id, study_name=f"T{r.id}", modality="CAR-T", values=[])
                for r in included]

    monkeypatch.setattr("core.sr.pipeline.gather_corpus", _gc)
    monkeypatch.setattr("core.sr.pipeline._screen_all", _sa)
    monkeypatch.setattr("core.sr.pipeline._extract_all", _ex)
    monkeypatch.setattr("core.sr.sentinel.check_named_trials",
                        lambda *a, **k: {"passed": True, "violations": [], "matched": {}})


def _stub_llm(monkeypatch):
    """Route the narrative + claim_check stages to deterministic offline stubs."""
    from core.models import ClaimCheck
    from core.tools.builtin import _NarrativeDraft

    def _parse(schema, system, user, *, stage="unknown", **kw):
        if schema is _NarrativeDraft:
            return _NarrativeDraft(
                abstract_background="Relapsed disease remains hard to treat [PMID: 111].",
                abstract_results="The ORR was 84% [PMID: 111].",
                abstract_conclusions="Cautious benefit was observed [PMID: 111].",
                introduction="Burden and rationale are established [PMID: 111].",
                discussion="Findings align with prior reports [PMID: 111].",
            )
        if schema is ClaimCheck:
            return ClaimCheck(finding_index=0, supported=True, notes="ok")
        raise AssertionError(f"unexpected schema {schema}")

    monkeypatch.setattr("core.llm.client.parse", _parse)


def test_narrative_stage_is_ordered_before_manuscript():
    order = [s.name for s in topo_order(engine.build_registry(_cfg()), "systematic")]
    assert "narrative" in order
    assert order.index("narrative") < order.index("manuscript")


def test_narrative_adapter_produces_prose_that_reaches_the_manuscript(tmp_path, monkeypatch):
    cfg, cites = _cfg(), _cites()
    _stub_chain(monkeypatch, cfg, cites)
    rd = engine.run_to(cfg, "assemble", created_utc=TS, runs_dir=tmp_path)

    _stub_llm(monkeypatch)
    ctx = RunContext(cfg=cfg, run_dir=rd, created_utc=TS)
    result = adapters.narrative_adapter(rd, ctx)

    assert (rd / "narrative_final.json").exists()
    assert (rd / "narrative.json").exists()
    assert "narrative_final.json" in result.outputs

    narrative = json.loads((rd / "narrative_final.json").read_text())
    # The abstract Conclusions stay locked (no reviewer sign-off in an auto run).
    from core.sr.signoff import DRAFT_BANNER
    assert narrative["abstract_conclusions"] == DRAFT_BANNER
    assert "84%" in narrative["abstract_results"]

    # Prose reaches the assembled components — no TODO stub in Discussion.
    from core.sr.manuscript.assemble import build_components
    from core.sr.manuscript.model import Note, Paragraph
    bundle_records = {"111": {"pmid": "111", "title": "T111", "year": 2024, "abstract": "x"},
                      "222": {"pmid": "222", "title": "T222", "year": 2024, "abstract": "x"}}
    from core.tools.sr_io import load_run_bundle
    bundle = load_run_bundle(rd, vertical=cfg.vertical)
    comps = build_components(bundle.run, cfg, bundle_records, narrative=narrative)
    disc = next(c for c in comps if c.key == "09_discussion")
    assert any(isinstance(b, Paragraph) and "prior reports" in b.text for b in disc.blocks)
    assert not any(isinstance(b, Note) and b.kind == "todo" for b in disc.blocks)
