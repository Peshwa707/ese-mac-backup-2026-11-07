"""Milestone-1 systematic-review orchestrator (protocol Steps 3-6, live).

Reuses the engine's grounded machinery end to end:
  PubMedConnector.search  ->  base.dedup  ->  verify.verify_citations  ->
  AI Reviewer-1 screening  ->  abstract-level extraction  ->  provenance log.

Every PMID that does not resolve against the live PubMed API is dropped before
screening (cite-or-omit at the corpus level); every extracted value is grounded
in the abstract or marked NR. Nothing is invented.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional

from core.connectors.base import merge_dedup
from core.connectors.pubmed import PubMedConnector
from core.models import Citation, IdType, Provenance, RetrievalResult
from core.render import provenance as provenance_store
from core.sr.config import SRConfig, load_sr_config
from core.sr.extract import extract_study
from core.sr.models import (
    ExtractedStudy,
    PrismaCounts,
    ScreenDecision,
    ScreeningResult,
    SRRun,
)
from core.sr.screen import screen_batch

_SCREEN_BATCH_SIZE = 10
_LLM_CONCURRENCY = 4


def _bucket_exclusion(reason: str, extra_buckets: list[dict] | None = None) -> str:
    """Categorize a screening-exclusion reason. Generic study-type buckets are
    built in; domain-specific buckets (e.g. an off-target or wrong-line bucket)
    come from the vertical's pico ``eligibility.exclusion_buckets``."""
    r = reason.lower()
    if any(k in r for k in ("preclinical", "in vitro", "animal")):
        return "preclinical/in-vitro/animal"
    if any(k in r for k in ("case report", "case series", "n < 10", "n<10")):
        return "case report/series"
    if any(k in r for k in ("review", "editorial", "commentary", "protocol-only", "no outcome")):
        return "review/editorial/no-outcome"
    for bucket in (extra_buckets or []):
        keys = [str(k).lower() for k in (bucket.get("keywords") or [])]
        if keys and any(k in r for k in keys):
            return bucket.get("label", "other / off-PICO")
    return "other / off-PICO"


async def _verify_seeds(connector: PubMedConnector, cfg: SRConfig) -> dict[str, bool]:
    """Reference-mining (Step 3b): confirm the prior-review seed PMIDs resolve.

    Honesty: full reference-list mining of these reviews is a manual / Milestone-2
    step; here we verify the seeds are real and flag the deeper mining as pending.
    """
    out: dict[str, bool] = {}
    for seed in cfg.reference_mining_seeds:
        pmid = seed.get("pmid", "")
        label = seed.get("label", pmid)
        c = Citation(id_type=IdType.PMID, id=pmid, source="pubmed")
        try:
            out[f"{label} (PMID {pmid})"] = await connector.verify(c)
        except Exception:
            out[f"{label} (PMID {pmid})"] = False
    return out


async def _run_llm(fn, *args):
    """Run a sync LLM call off the event loop."""
    return await asyncio.to_thread(fn, *args)


async def _screen_all(citations: list[Citation], cfg: SRConfig) -> list[ScreeningResult]:
    batches = [
        citations[i : i + _SCREEN_BATCH_SIZE]
        for i in range(0, len(citations), _SCREEN_BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def _one(batch):
        async with sem:
            return await _run_llm(screen_batch, batch, cfg)

    results = await asyncio.gather(*(_one(b) for b in batches))
    return [r for batch in results for r in batch]


async def _extract_all(citations: list[Citation], cfg: SRConfig) -> list[ExtractedStudy]:
    sem = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def _one(c):
        async with sem:
            return await _run_llm(extract_study, c, cfg)

    return list(await asyncio.gather(*(_one(c) for c in citations)))


def _named_trial_capture(cfg: SRConfig, citations: list[Citation]) -> dict[str, bool]:
    blob = " \n ".join(f"{c.title} {c.snippet}" for c in citations).lower()
    return {trial: (trial.lower() in blob) for trial in cfg.named_trials}


_LIMITATIONS = [
    "AI = Reviewer 1 only; every decision and value requires independent human (Reviewer 2) verification.",
    "Extraction is primarily ABSTRACT-LEVEL; fields reported only in full-text tables are marked NR until full text (PMC/Unpaywall/user PDFs) is available.",
    "PubMed, Europe PMC, and ClinicalTrials.gov are executed live; Embase/CENTRAL/Web of Science are included only if exported records are imported (provenance-attested).",
    "Conference abstracts beyond Europe PMC coverage and exhaustive reference-list mining are flagged for manual completion.",
    "Pooled proportions and hazard ratios are computed (random-effects); Bayesian NMA runs only where a connected comparator network exists (else reported infeasible, never fabricated).",
]


@dataclass
class Corpus:
    """The no-LLM grounding phase output: a verified, deduped citation set.

    Produced by ``gather_corpus`` using only PubMed (no ANTHROPIC_API_KEY needed),
    so the LLM screening/extraction can be supplied EITHER by the engine's
    llm.client OR by an external orchestrator (e.g. harness agents).
    """

    cfg: SRConfig
    run_id: str
    search_result: RetrievalResult
    identified: list[Citation]
    deduped: list[Citation]
    verified: list[Citation]
    rejected: list[dict]
    seed_verification: dict[str, bool]
    per_source: dict = None            # {source_name: n_records}
    sources_executed: list = None      # source names that returned results


def _pmid_of(c: Citation) -> Optional[str]:
    if c.id_type == IdType.PMID:
        return c.id
    return (c.extra_ids or {}).get("PMID")


def _doi_of(c: Citation) -> Optional[str]:
    if c.doi:
        return c.doi
    if c.id_type == IdType.DOI:
        return c.id
    return (c.extra_ids or {}).get("DOI")


async def gather_corpus(
    cfg: Optional[SRConfig] = None,
    *,
    retmax: Optional[int] = None,
    connector: Optional[PubMedConnector] = None,
    sources: Optional[list] = None,
    close_sources: bool = False,
) -> Corpus:
    """Steps 3 + corpus-level cite-or-omit across one or more sources. No LLM/key.

    Each source is queried (fail-soft), results are cross-source merged, then every
    record is verified: by PMID (PubMed), by DOI (Crossref), or — for imported
    records from authenticated DB exports — attested (flagged distinctly from live).
    """
    cfg = cfg or load_sr_config()
    own_sources = False
    if sources is None:
        sources = [connector] if connector is not None else [PubMedConnector(retmax=retmax or cfg.retmax)]
        own_sources = connector is None
    pubmed = next((s for s in sources if isinstance(s, PubMedConnector)), None)
    own_pubmed = pubmed is None
    if pubmed is None:
        pubmed = PubMedConnector()
    try:
        identified: list[Citation] = []
        per_source: dict[str, int] = {}
        sources_executed: list[str] = []
        for s in sources:
            res = await s.search(cfg.source_query(s.source), {})
            cites = list(res.citations)
            per_source[s.source] = per_source.get(s.source, 0) + len(cites)
            if res.available:
                sources_executed.append(s.source)
            for c in cites:
                if c.provenance and c.verification == "unverified":
                    c.verification = "attested"   # imported from an authenticated export
            identified.extend(cites)

        seed_verification = await _verify_seeds(pubmed, cfg)
        merged = merge_dedup(identified)

        # Verify by PMID (PubMed bulk) and DOI (Crossref); attested records pass on provenance.
        pmids = [p for c in merged if (p := _pmid_of(c))]
        resolved_pmids = await pubmed.verify_bulk(pmids) if pmids else set()
        from core.connectors.crossref import verify_dois
        dois = [d for c in merged if (d := _doi_of(c))]
        resolved_dois = {d.lower() for d in (await verify_dois(dois) if dois else set())}

        # NCT registrations (ClinicalTrials.gov) verify against the CT.gov API.
        nct_cites = [c for c in merged if c.id_type == IdType.NCT]
        resolved_ncts: set[str] = set()
        if nct_cites:
            from core.connectors.clinicaltrials import ClinicalTrialsConnector
            ct = next((s for s in sources if isinstance(s, ClinicalTrialsConnector)), None)
            own_ct = ct is None
            ct = ct or ClinicalTrialsConnector()
            oks = await asyncio.gather(*(ct.verify(c) for c in nct_cites))
            resolved_ncts = {c.id for c, ok in zip(nct_cites, oks) if ok}
            if own_ct:
                await ct.aclose()

        verified: list[Citation] = []
        rejected: list[dict] = []
        for c in merged:
            p, d = _pmid_of(c), _doi_of(c)
            if p and p in resolved_pmids:
                c.verified, c.verification = True, "live"
            elif d and d.lower() in resolved_dois:
                c.verified, c.verification = True, "live"
            elif c.id_type == IdType.NCT and c.id in resolved_ncts:
                c.verified, c.verification = True, "live"
            elif c.verification == "attested":
                c.verified = True             # provenance-attested (export file logged)
            else:
                c.verified = False
            (verified if c.verified else rejected).append(
                c if c.verified else {"key": c.key, "source": c.source, "reason": "unresolved"})

        search_result = RetrievalResult(source="+".join(per_source), query=cfg.pubmed_query,
                                        citations=identified)
        return Corpus(
            cfg=cfg, run_id=uuid.uuid4().hex, search_result=search_result,
            identified=identified, deduped=merged, verified=verified, rejected=rejected,
            seed_verification=seed_verification, per_source=per_source,
            sources_executed=sources_executed,
        )
    finally:
        # Close the passed-in sources when the caller cedes ownership (close_sources),
        # or when gather_corpus created them itself (own_sources). Guard against
        # double-closing the verification pubmed (closed separately below only if we
        # created it, i.e. it was not among `sources`).
        if own_sources or close_sources:
            for s in sources:
                await s.aclose()
        if own_pubmed:
            await pubmed.aclose()


def build_run(
    corpus: Corpus,
    screening: list[ScreeningResult],
    extractions: list[ExtractedStudy],
    prescreen: Optional[dict] = None,
) -> SRRun:
    """Assemble the final SRRun from a corpus + LLM-stage results. Writes provenance.

    Source-agnostic: ``screening``/``extractions`` may come from the engine's
    llm.client or from harness agents — the counts, exclusion buckets, named-trial
    capture, and provenance are computed identically either way.
    """
    cfg = corpus.cfg
    verified_ids = {c.id for c in corpus.verified}
    included_pmids = {
        r.pmid for r in screening
        if r.decision == ScreenDecision.INCLUDE and r.pmid in verified_ids
    }
    n_excluded = sum(1 for r in screening if r.decision == ScreenDecision.EXCLUDE)
    n_uncertain = sum(1 for r in screening if r.decision == ScreenDecision.UNCERTAIN)
    exclusion_reasons: dict[str, int] = {}
    for r in screening:
        if r.decision == ScreenDecision.EXCLUDE:
            b = _bucket_exclusion(r.reason, cfg.exclusion_buckets)
            exclusion_reasons[b] = exclusion_reasons.get(b, 0) + 1

    # Named-trial sentinel (positive control): every configured pivotal trial must
    # have survived screening (reached include/uncertain). Fails the run under
    # cfg.sentinel_strict; otherwise recorded as a prominent warning (WP4).
    from core.sr.sentinel import check_named_trials, SentinelViolation
    _dec_by_pmid = {r.pmid: r.decision for r in screening}
    _screened_recs = [
        {"pmid": c.id, "title": c.title, "abstract": c.snippet,
         "decision": _dec_by_pmid.get(c.id, "")}
        for c in corpus.verified
    ]
    sentinel_report = (
        check_named_trials(cfg.named_trials, _screened_recs, min_stage="advance")
        if cfg.named_trials else {"passed": True, "violations": [], "matched": {}}
    )
    if not sentinel_report["passed"] and cfg.sentinel_strict:
        raise SentinelViolation(
            f"Named-trial sentinel failed — these pivotal trials did not survive "
            f"screening: {sentinel_report['violations']} (matched={sentinel_report['matched']})"
        )

    per_source = corpus.per_source or {"pubmed": len(corpus.identified)}
    # When a publication-type pre-screen ran, records removed by type never reached
    # title/abstract screening — so 'screened' must exclude them (else PRISMA/manuscript
    # over-report screening volume). Falls back to verified count when no prescreen.
    _pc = (prescreen or {}).get("counts") or {}
    prescreen_excluded = _pc.get("exclude_by_type", 0) + _pc.get("conference_abstract", 0)
    screened_count = _pc.get("screen", len(corpus.verified)) if _pc else len(corpus.verified)
    counts = PrismaCounts(
        identified_pubmed=per_source.get("pubmed", 0),
        identified_other_sources=sum(v for k, v in per_source.items() if k != "pubmed"),
        after_dedup=len(corpus.deduped),
        id_unverified_removed=len(corpus.rejected),
        prescreen_excluded=prescreen_excluded,
        screened=screened_count,
        excluded_screening=n_excluded,
        included=len(included_pmids),
        studies_included=len(extractions) if extractions else len(included_pmids),
        uncertain=n_uncertain,
    )

    from core.llm import client as _llm
    provenance = Provenance(
        report_id=corpus.run_id,
        queries=[cfg.pubmed_query],
        raw_results=[corpus.search_result],
        verification_trail=[{"pmid": c.id, "verified": c.verified} for c in corpus.deduped],
        rejected=corpus.rejected,
        stage_models=_llm.stage_log(),   # which model/backend served which stage
    )
    provenance_id = provenance_store.write(provenance)

    return SRRun(
        run_id=corpus.run_id,
        review_title=cfg.review_title,
        pubmed_query=cfg.pubmed_query,
        counts=counts,
        exclusion_reasons=exclusion_reasons,
        screening=screening,
        extractions=extractions,
        named_trial_capture=_named_trial_capture(cfg, corpus.verified),
        seed_verification=corpus.seed_verification,
        per_source=per_source,
        sources_executed=list(corpus.sources_executed or []),
        prescreen=prescreen or {},
        sentinel=sentinel_report,
        n_attested=sum(1 for c in corpus.verified if c.verification == "attested"),
        limitations=list(_LIMITATIONS),
        provenance_id=provenance_id,
        disclaimer=cfg.disclaimer,
    )


async def run_sr(
    cfg: Optional[SRConfig] = None,
    *,
    retmax: Optional[int] = None,
    connector: Optional[PubMedConnector] = None,
    do_extract: bool = True,
) -> SRRun:
    """Full engine path (Steps 3-6). Thin wrapper over the orchestrator engine — the
    single canonical lifecycle (run state, content hashes, honesty gates, resume) —
    so the CLI and direct callers no longer use a second hand-rolled orchestrator.

    Drives the sync orchestrator in a worker thread (its stage adapters own their event
    loops) and returns the assembled SRRun. Requires ANTHROPIC_API_KEY for live LLM
    stages; the agent-driven path still calls ``gather_corpus`` -> (agents) ->
    ``build_run`` directly.
    """
    import json
    from datetime import datetime, timezone

    from core.llm import client as _llm
    from core.sr.orchestrator import engine

    cfg = cfg or load_sr_config()
    _llm.reset_stage_log()                       # fresh per-stage model provenance per run
    created = datetime.now(timezone.utc).isoformat()
    run_dir = await asyncio.to_thread(
        engine.run_to, cfg, "assemble",
        created_utc=created, retmax=retmax, connector=connector, do_extract=do_extract,
    )
    return SRRun.model_validate(json.loads((run_dir / "srrun.json").read_text(encoding="utf-8")))
