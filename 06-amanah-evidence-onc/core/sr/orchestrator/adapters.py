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
        from core.sr.models import RoBAssessment
        rob = [RoBAssessment.model_validate(x) for x in read_json(run_dir, "rob.json")]
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


def search_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """Multi-source grounding: search + dedup + cite-or-omit verify (spec stage 6)."""
    from core.sr.agent_io import dump_corpus
    from core.sr.orchestrator import provenance as prov   # lazy: avoid import cycle
    from core.sr.pipeline import gather_corpus

    corpus = run_async(gather_corpus(ctx.cfg, retmax=ctx.retmax, connector=ctx.connector))
    path = Path(dump_corpus(corpus, Path(run_dir) / "corpus.json"))
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
