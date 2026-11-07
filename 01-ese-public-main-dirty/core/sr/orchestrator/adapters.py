"""Uniform stage adapters over the existing pipeline functions (WP-M1.4).

Each existing stage function has its own signature (sync/async, single-item/batch,
Citation/Corpus/Path). An *adapter* normalizes it to ``fn(run_dir, ctx) -> StageResult``:
it reads its inputs from artifacts under ``run_dir`` (reusing ``core.sr.agent_io``),
calls the underlying function, writes hashed JSON artifacts, and returns the produced
paths + a scrubbed human summary.

WP-M1.4a covers the framework + the deterministic adapters (stats, grade). The
LLM/async adapters (search, screen, fulltext, extract, manuscript) follow in M1.4b.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
from pathlib import Path

from core.sr.orchestrator.hashing import hash_file
from core.sr.orchestrator.stages import RunContext, StageResult


# --------------------------------------------------------------------- framework
def to_jsonable(obj):
    """Convert a dataclass / pydantic model / container to JSON-ready data. Non-finite
    floats (NaN/Inf, which R stats can emit) become None so artifacts stay valid JSON
    and reproducibility hashes don't depend on lenient parsing."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(dataclasses.asdict(obj))
    if hasattr(obj, "model_dump"):                    # pydantic v2
        return to_jsonable(obj.model_dump(mode="json"))
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def write_json(run_dir: Path, name: str, obj) -> tuple[Path, str]:
    """Write ``obj`` as JSON under ``run_dir/name``; return (path, sha256)."""
    path = Path(run_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys for a canonical, key-order-independent artifact so reproducibility
    # hashes are stable regardless of dict insertion order. allow_nan=False so a stray
    # non-finite that slipped past to_jsonable fails loudly instead of writing NaN tokens.
    path.write_text(json.dumps(to_jsonable(obj), indent=2, sort_keys=True,
                               default=str, allow_nan=False), encoding="utf-8")
    return path, hash_file(path)


def read_json(run_dir: Path, name: str):
    return json.loads((Path(run_dir) / name).read_text(encoding="utf-8"))


def run_async(coro):
    """Run a coroutine from a synchronous adapter; refuse if a loop is already running
    (the caller must not nest event loops — a future async surface owns its own loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if asyncio.iscoroutine(coro):
        coro.close()   # don't leak an un-awaited coroutine on the refusal path
    raise RuntimeError("run_async() must not be called from within a running event loop")


# --------------------------------------------------------------- deterministic adapters
def stats_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Pool proportions + hazard ratios (+ NMA) from extractions.json (spec stage 13)."""
    from core.sr.agent_io import load_extractions
    from core.sr.stats.run import meta_analyze

    extractions = load_extractions(read_json(run_dir, "extractions.json"))
    modality_by_pmid = {e.pmid: (e.modality or "") for e in extractions}
    fig_dir = Path(run_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    res = meta_analyze(extractions, modality_by_pmid, fig_dir, cfg=ctx.cfg)
    path, _ = write_json(run_dir, "stats.json", res)
    summary = {
        "available": res.available,
        "n_proportions": len(res.outcomes),
        "n_hazard_ratios": len(res.hr_outcomes),
        "note": (res.note or "")[:200],
    }
    return StageResult(
        outputs={"stats.json": path},
        summary=summary,
        deterministic_inputs={"extractions.json": hash_file(Path(run_dir) / "extractions.json")},
    )


def grade_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Deterministic GRADE certainty per outcome (spec stage 12)."""
    from core.sr.agent_io import load_extractions, load_screening
    from core.sr.grade import grade_outcomes

    extractions = load_extractions(read_json(run_dir, "extractions.json"))
    screening = load_screening(read_json(run_dir, "screening.json"))
    rob = None
    rob_path = Path(run_dir) / "rob.json"
    if rob_path.exists():
        # Use the format-tolerant loader: rob.json is written as a LIST by rob_adapter but
        # as a DICT ({"assessments": [...]}) by the builtin risk-of-bias tool. Iterating the
        # dict here would validate its string keys as RoBAssessments and crash GRADE.
        from core.tools.sr_io import load_rob
        rob = load_rob(run_dir)
    rows = grade_outcomes(extractions, screening, rob)
    path, _ = write_json(run_dir, "grade.json", rows)
    consumed = {
        "extractions.json": hash_file(Path(run_dir) / "extractions.json"),
        "screening.json": hash_file(Path(run_dir) / "screening.json"),
    }
    if rob_path.exists():
        consumed["rob.json"] = hash_file(rob_path)   # only when ROB actually fed GRADE
    return StageResult(outputs={"grade.json": path},
                       summary={"n_outcomes_graded": len(rows)},
                       deterministic_inputs=consumed)


# ---------------------------------------------------------------- LLM / async adapters
def _dump_citations(run_dir: Path, name: str, cites: list) -> tuple[Path, str]:
    return write_json(run_dir, name, [c.model_dump(mode="json") for c in cites])


def _load_citations(run_dir: Path, name: str) -> list:
    from core.models import Citation
    return [Citation.model_validate(d) for d in read_json(run_dir, name)]


def _hash_of(run_dir: Path, name: str) -> str:
    return hash_file(Path(run_dir) / name)


def _live_source_connectors(cfg, retmax, base_connector):
    """Build the live search connectors from search_strategies.yaml blocks marked
    `executed: true`. Each top-level block whose name maps to a known live connector
    and carries a query is instantiated, so a vertical that declares EuropePMC +
    ClinicalTrials `executed: true` (like antithrombin-preeclampsia) actually searches
    them instead of PubMed-only. PubMed remains the verification backbone in
    gather_corpus regardless.

    If a caller injected an explicit `base_connector` (tests, custom drivers), that
    exact connector is honored unchanged. If no block is marked executed, falls back
    to PubMed so behavior never regresses to searching nothing.
    """
    from core.connectors.pubmed import PubMedConnector
    if base_connector is not None:
        return [base_connector]

    search = getattr(cfg, "search", {}) or {}

    def _europepmc():
        from core.connectors.europepmc import EuropePmcConnector
        return EuropePmcConnector(page_size=int((search.get("europepmc") or {}).get("page_size", 200)))

    def _clinicaltrials():
        from core.connectors.clinicaltrials import ClinicalTrialsConnector
        return ClinicalTrialsConnector(page_size=int((search.get("clinicaltrials") or {}).get("page_size", 20)))

    def _webofscience():
        # Credentialed API (Clarivate). Self-degrades to `unavailable` when no
        # WOS_STARTER_KEY is configured, so marking it executed is safe pre-key.
        from core.connectors.webofscience import WebOfScienceConnector
        block = search.get("webofscience") or {}
        return WebOfScienceConnector(page_size=int(block.get("page_size", 50)),
                                     db=str(block.get("db", "WOS")))

    def _scopus():
        # Credentialed API (Elsevier). Self-degrades to `unavailable` when no
        # SCOPUS_API_KEY is configured; SCOPUS_INSTTOKEN (optional) unlocks full results.
        from core.connectors.scopus import ScopusConnector
        block = search.get("scopus") or {}
        return ScopusConnector(page_size=int(block.get("page_size", 25)),
                               view=str(block.get("view", "STANDARD")))

    ctors = {
        "pubmed": lambda: PubMedConnector(retmax=retmax or cfg.retmax),
        "europepmc": _europepmc,
        "clinicaltrials": _clinicaltrials,
        "webofscience": _webofscience,
        "scopus": _scopus,
    }
    out = []
    for name, ctor in ctors.items():
        block = search.get(name)
        if not (isinstance(block, dict) and block.get("executed")):
            continue
        if not block.get("query"):
            # Fail loud: a source marked executed but missing a query would silently
            # under-search and misreport PRISMA "databases searched" — an integrity defect.
            raise ValueError(
                f"search source {name!r} is marked executed: true but has no query "
                f"(fix search_strategies.yaml)")
        out.append(ctor())
    if not out:
        # No source declared executed — fall back to PubMed so a run never searches
        # nothing. (PubMed's verification role in gather_corpus is separate and always
        # runs, so we do NOT force PubMed into the search set when a vertical
        # deliberately executes only other sources.)
        out.append(PubMedConnector(retmax=retmax or cfg.retmax))
    return out


def search_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Multi-source grounding: search + dedup + cite-or-omit verify (spec stage 6)."""
    import os
    from core.sr.agent_io import dump_corpus
    from core.sr.orchestrator import provenance as prov   # lazy: avoid import cycle
    from core.sr.pipeline import gather_corpus

    # Live sources = every search_strategies.yaml block marked `executed: true`.
    sources = _live_source_connectors(ctx.cfg, ctx.retmax, ctx.connector)

    # Imported-corpus merge (Embase/CENTRAL/Scopus exports, Google Scholar), scoped to
    # THIS review's vertical inbox so one review's exports never leak into another.
    # Additive and side-effect-free: imported_connectors() reads the inbox but never
    # creates it, so an empty/absent inbox is a no-op. Set ESE_DISABLE_IMPORTS=1 to skip.
    if os.environ.get("ESE_DISABLE_IMPORTS") != "1" and getattr(ctx.cfg, "vertical", ""):
        import sys as _sys
        from core.connectors.imported import (IMPORTS_DIR, imported_connectors,
                                              legacy_flat_exports, vertical_inbox)
        imps = imported_connectors(vertical_inbox(ctx.cfg.vertical, IMPORTS_DIR))
        if imps:
            sources = [*sources, *imps]
        # Surface (don't silently drop) exports left in the old flat layout, which the
        # per-vertical scan no longer reads — silent under-search is an integrity defect.
        orphaned = legacy_flat_exports(IMPORTS_DIR)
        if orphaned:
            print(f"WARNING: import exports found in the OLD flat layout and NOT read "
                  f"({', '.join(orphaned)} under {IMPORTS_DIR}). Move each to "
                  f"{IMPORTS_DIR}/<vertical>/<database>/ to include it — e.g. "
                  f"{IMPORTS_DIR}/{ctx.cfg.vertical}/Embase/.", file=_sys.stderr)

    # close_sources=True hands ownership to gather_corpus, whose async finally closes the
    # connectors' httpx clients in the right event-loop context (the adapter always builds
    # `sources`, so without this they would leak per run in the long-lived API server).
    # An injected ctx.connector is owned by the caller → don't ask gather_corpus to close it.
    corpus = run_async(gather_corpus(ctx.cfg, retmax=ctx.retmax, sources=sources,
                                     close_sources=ctx.connector is None))
    path = Path(dump_corpus(corpus, Path(run_dir) / "corpus.json"))
    from core.sr.agent_io import records_for_agents
    write_json(run_dir, "records_for_agents.json", records_for_agents(corpus))
    # freeze the returned-record set for replay (PRISMA-S + reproducibility provenance)
    prov.write_search_manifest(run_dir, corpus, timestamp=ctx.created_utc)
    return StageResult(
        outputs={"corpus.json": path},
        summary={"identified": len(corpus.identified), "verified": len(corpus.verified),
                 "rejected": len(corpus.rejected)},
    )


def prescreen_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Deterministic publication-type pre-screen before LLM screening (spec stage 7)."""
    from core.sr.agent_io import load_corpus
    from core.sr.prescreen import prescreen_records

    corpus = load_corpus(Path(run_dir) / "corpus.json", ctx.cfg)
    to_screen, report = prescreen_records(corpus.verified)
    sp, _ = write_json(run_dir, "prescreen.json", report)
    tp, _ = _dump_citations(run_dir, "to_screen.json", to_screen)
    return StageResult(
        outputs={"prescreen.json": sp, "to_screen.json": tp},
        summary={"counts": report["counts"]},
        deterministic_inputs={"corpus.json": _hash_of(run_dir, "corpus.json")},
    )


def screen_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Reviewer-1 title/abstract screening (spec stage 8; dual-LLM is M2)."""
    from collections import Counter
    from core.sr.pipeline import _screen_all

    to_screen = _load_citations(run_dir, "to_screen.json")
    # Use the pipeline's batched/concurrent wrapper (same surface run_sr drives), so a
    # single canonical screening path serves both the orchestrator and legacy callers.
    results = run_async(_screen_all(to_screen, ctx.cfg)) if to_screen else []
    path, _ = write_json(run_dir, "screening.json", results)
    decisions = Counter(str(getattr(r.decision, "value", r.decision)) for r in results)
    return StageResult(
        outputs={"screening.json": path},
        summary={"n": len(results), "decisions": dict(decisions)},
        deterministic_inputs={"to_screen.json": _hash_of(run_dir, "to_screen.json")},
    )


def _included_citations(run_dir: Path, ctx: RunContext) -> list:
    from core.sr.agent_io import load_corpus, load_screening
    from core.sr.models import ScreenDecision

    corpus = load_corpus(Path(run_dir) / "corpus.json", ctx.cfg)
    screening = load_screening(read_json(run_dir, "screening.json"))
    by_pmid = {c.id: c for c in corpus.verified}
    return [by_pmid[r.pmid] for r in screening
            if r.decision == ScreenDecision.INCLUDE and r.pmid in by_pmid]


def fulltext_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Resolve full text (PMC / Unpaywall / inbox) for included studies (spec stage 9)."""
    from core.sr.fulltext import coverage_summary, resolve_many

    included = _included_citations(run_dir, ctx)
    pmids = [c.id for c in included]
    results = run_async(resolve_many(pmids)) if pmids else []
    cov = coverage_summary(results)
    path, _ = write_json(run_dir, "fulltext_coverage.json", cov)
    return StageResult(
        outputs={"fulltext_coverage.json": path},
        summary=cov,
        deterministic_inputs={"corpus.json": _hash_of(run_dir, "corpus.json"),
                              "screening.json": _hash_of(run_dir, "screening.json")},
    )


def extract_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Abstract/full-text data extraction with NR/quotes (spec stage 10)."""
    from core.sr.pipeline import _extract_all

    included = _included_citations(run_dir, ctx)
    if not ctx.do_extract:
        extractions = []                      # legacy --no-extract: produce an empty set
    else:
        extractions = run_async(_extract_all(included, ctx.cfg)) if included else []
    path, _ = write_json(run_dir, "extractions.json", extractions)
    return StageResult(
        outputs={"extractions.json": path},
        summary={"n": len(extractions)},
        deterministic_inputs={"corpus.json": _hash_of(run_dir, "corpus.json"),
                              "screening.json": _hash_of(run_dir, "screening.json")},
    )


def assemble_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Assemble the final SRRun from corpus + screening + extractions (build_run).

    The named-trial sentinel runs INSIDE build_run (fails closed under
    cfg.sentinel_strict) — i.e. the honesty gate now lives in the orchestrated chain,
    not in the legacy run_sr."""
    from core.sr.agent_io import load_corpus, load_extractions, load_screening
    from core.sr.pipeline import build_run

    corpus = load_corpus(Path(run_dir) / "corpus.json", ctx.cfg)
    screening = load_screening(read_json(run_dir, "screening.json"))
    extractions = load_extractions(read_json(run_dir, "extractions.json"))
    prescreen = (read_json(run_dir, "prescreen.json")
                 if (Path(run_dir) / "prescreen.json").exists() else None)
    run = build_run(corpus, screening, extractions, prescreen=prescreen)
    path, _ = write_json(run_dir, "srrun.json", run)
    consumed = {n: _hash_of(run_dir, n)
                for n in ("corpus.json", "screening.json", "extractions.json")}
    if prescreen is not None:
        consumed["prescreen.json"] = _hash_of(run_dir, "prescreen.json")

    # Consume the §7b evidence-strength report so suppression/caveats reach the assembled
    # dossier (full manuscript-render enforcement lives in the export subsystem).
    es_summary = {}
    es_path = Path(run_dir) / "evidence_strength.json"
    if es_path.exists():
        consumed["evidence_strength.json"] = _hash_of(run_dir, "evidence_strength.json")
        es = read_json(run_dir, "evidence_strength.json")
        es_summary = {"n_suppressed": es.get("n_suppressed"),
                      "n_non_comparative": es.get("n_non_comparative"),
                      "n_surrogate": es.get("n_surrogate")}
    return StageResult(
        outputs={"srrun.json": path},
        summary={"included": run.counts.included, "studies_included": run.counts.studies_included,
                 "sentinel_passed": run.sentinel.get("passed", True),
                 "evidence_strength": es_summary},
        deterministic_inputs=consumed,
    )


def rob_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Risk-of-bias assessments per included study (spec stage 11).

    Reads ``rob.json`` when harness agents have already written it; otherwise emits an
    empty list so downstream GRADE can proceed with RoB omitted (honest, not fabricated).
    """
    rob_path = Path(run_dir) / "rob.json"
    if rob_path.exists():
        data = read_json(run_dir, "rob.json")
        return StageResult(
            outputs={"rob.json": rob_path},
            summary={"n": len(data), "source": "existing"},
            deterministic_inputs={"extractions.json": _hash_of(run_dir, "extractions.json")},
        )
    path, _ = write_json(run_dir, "rob.json", [])
    return StageResult(
        outputs={"rob.json": path},
        summary={"n": 0, "note": "no RoB assessments yet — run harness agents or re-stage"},
        deterministic_inputs={"extractions.json": _hash_of(run_dir, "extractions.json")},
    )


def narrative_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """AI-drafted, citation-checked narrative prose (spec stage 14).

    Reuses the exact grounded-brief → draft → per-claim support-check path that the
    registered ``narrative`` tool runs (``core.tools.builtin._run_narrative``): it
    produces ``narrative_final.json`` (the prose ``ese export`` renders) plus a
    ``narrative.json`` provenance record.

    ``narrative`` is a REQUIRED stage: every profile lists it and ``manuscript`` depends
    on it, so a run reaching manuscript through the orchestrator always runs this stage
    (a cloud LLM is therefore required to complete an orchestrated run). Only the
    standalone ``ese export`` CLI is additive — it renders ``narrative_final.json`` if
    present and falls back to the deterministic TODO stubs if not. The orchestrator writes
    ``screening.json`` but not the builtin-tool ``includes.json``; derive it here from the
    INCLUDE decisions so the reused function finds its included-study pool.
    """
    from core.sr.agent_io import load_screening
    from core.sr.models import ScreenDecision
    from core.tools.builtin import _run_narrative

    run_dir = Path(run_dir)
    inc_path = run_dir / "includes.json"
    if not inc_path.exists():
        screening = load_screening(read_json(run_dir, "screening.json"))
        includes = [s.pmid for s in screening if s.decision == ScreenDecision.INCLUDE]
        inc_path.write_text(json.dumps(includes, indent=2), encoding="utf-8")

    out = _run_narrative(run_dir, {"vertical": getattr(ctx.cfg, "vertical", None)}, "cloud")
    provenance = read_json(run_dir, "narrative.json")
    summary = {
        "locked": provenance.get("locked"),
        "cited_valid": provenance.get("cited_valid", []),
        "claim_check": {k: provenance.get("claim_check", {}).get(k)
                        for k in ("mode", "kept", "dropped_total", "supported")},
    }
    consumed = {"screening.json": _hash_of(run_dir, "screening.json"),
                "extractions.json": _hash_of(run_dir, "extractions.json")}
    return StageResult(
        outputs={"narrative.json": out, "narrative_final.json": run_dir / "narrative_final.json"},
        summary=summary,
        deterministic_inputs=consumed,
    )


def manuscript_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Mark the run ready for CLI export (spec stage 15).

    Actual MD/DOCX/PDF rendering is invoked by ``ese export`` after ``export_gate`` clears.
    """
    from core.sr.orchestrator import engine as eng

    gate = eng.export_gate(run_dir, cfg=ctx.cfg)
    status = {"gate": gate, "note": "invoke ese export after gates clear"}
    path, _ = write_json(run_dir, "manuscript_status.json", status)
    ok = gate is None or (gate[0] is True)
    return StageResult(
        outputs={"manuscript_status.json": path},
        summary={"export_allowed": ok, "gate": gate},
        deterministic_inputs={"srrun.json": _hash_of(run_dir, "srrun.json")},
    )
