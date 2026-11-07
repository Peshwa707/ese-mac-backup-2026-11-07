"""Front-end stages: idea -> structured protocol (M3).

These stages run before the existing search→manuscript pipeline. They turn a rough
research idea into a structured PICO (WP-M3.1) and, in later WPs, check novelty,
scope feasibility, and generate a reusable vertical config. LLM stages route through
the M2 gateway with the Reviewer-1 spec and content_class="metadata" (no clinical
full text egresses here). Every study/PMID a front-end stage emits must be
live-verified or flagged — the no-fabrication contract extends to the front-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

import re
import shutil

import yaml

_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

from core.llm.gateway import gateway_parse
from core.sr.orchestrator.adapters import _hash_of, read_json, run_async, write_json
from core.sr.orchestrator.hashing import hash_json
from core.sr.orchestrator.reviewers import resolve_reviewer
from core.sr.orchestrator.stages import RunContext, StageResult


class PicoDraft(BaseModel):
    """A structured review question drafted from a free-text idea."""
    framing: Literal["PICO", "PCC"] = "PICO"
    population: str
    intervention: str
    comparator: str = ""
    outcomes: list[str] = Field(default_factory=list)
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)


_PICO_SYSTEM = (
    "You are a systematic-review methodologist. Convert the researcher's rough idea into "
    "a structured review question. Choose PICO for intervention questions or PCC for "
    "scoping questions. Populate population, intervention/exposure, comparator (empty if "
    "none/single-arm), the key outcomes, and explicit inclusion and exclusion criteria. "
    "Do NOT invent study names, citations, or facts — structure ONLY what the idea implies."
)


def idea_to_pico_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """WP-M3.1 — draft a structured PICO/PCC from runs/<id>/idea.txt."""
    idea_path = Path(run_dir) / "idea.txt"
    if not idea_path.exists():
        raise FileNotFoundError(f"{idea_path} (write the rough research idea there first)")
    idea = idea_path.read_text(encoding="utf-8").strip()
    if not idea:
        raise ValueError("idea.txt is empty")

    spec = resolve_reviewer(ctx.cfg, "r1")
    draft = gateway_parse(PicoDraft, _PICO_SYSTEM, idea, stage="idea_to_pico",
                          spec=spec, content_class="metadata", max_tokens=4000)
    path, _ = write_json(run_dir, "pico.json", draft)
    return StageResult(
        outputs={"pico.json": path},
        summary={"framing": draft.framing, "n_outcomes": len(draft.outcomes),
                 "n_inclusion": len(draft.inclusion_criteria),
                 "n_exclusion": len(draft.exclusion_criteria)},
        deterministic_inputs={"idea.txt": _hash_of(run_dir, "idea.txt")},
    )


# --------------------------------------------------------------------- WP-M3.2 novelty
class NoveltyAssessment(BaseModel):
    duplication: Literal["none", "partial", "full"]
    gap_statement: str
    recommendation: Literal["proceed", "refine", "stop"]


_NOVELTY_SYSTEM = (
    "You assess whether a proposed systematic review duplicates existing reviews. You are "
    "given ONLY verified prior reviews (real PMIDs). Judge duplication (none/partial/full), "
    "state the evidence gap the new review would fill, and recommend proceed/refine/stop. "
    "Do NOT invent or cite any PMID — refer only to the provided list."
)


def _pubmed_phrase(term: str) -> str:
    """Quote a free-text PICO term as a PubMed phrase so embedded operators/field tags
    can't broaden or break the scoping query."""
    cleaned = term.replace('"', " ").replace("[", " ").replace("]", " ").strip()
    return f'"{cleaned}"' if cleaned else '""'


async def _verified_records(query: str, *, connector=None, retmax: int = 50) -> list[dict]:
    """Search PubMed and return ONLY live-verified records (no fabrication). Fails closed
    on an outage at search OR verifier level so missing data can't read as 'none found'."""
    from core.connectors.pubmed import PubMedConnector
    own = connector is None
    pm = connector or PubMedConnector(retmax=retmax)
    try:
        res = await pm.search(query, {})
        if not getattr(res, "available", True):
            # PubMed failed soft (outage/error). Treating empty results as "no prior
            # reviews" would be a false novelty pass — fail closed instead.
            raise RuntimeError(
                "PubMed search unavailable — refusing to proceed on missing data")
        cites = list(res.citations)
        ids = [c.id for c in cites if c.id]
        resolved = await pm.verify_bulk(ids) if ids else set()
        if ids and not resolved:
            # verify_bulk fails soft (empty set) on a verifier outage; a PubMed search
            # that returned records but verified none is almost certainly an outage, not a
            # genuine "no prior reviews" — fail closed rather than false-pass novelty.
            raise RuntimeError(
                "PubMed PMID verification unavailable (records found but none verified) — "
                "refusing to assess novelty on unverifiable data")
        return [{"pmid": c.id, "title": c.title} for c in cites if c.id in resolved]
    finally:
        if own:
            await pm.aclose()


async def _verified_prior_reviews(query: str, *, connector=None, retmax: int = 50) -> list[dict]:
    """Prior SR/MA (verified). Thin alias over _verified_records for the novelty stage."""
    return await _verified_records(query, connector=connector, retmax=retmax)


def novelty_check_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """WP-M3.2 — find prior SR/MA (verified PMIDs only) and assess duplication/gap."""
    pico = PicoDraft.model_validate(read_json(run_dir, "pico.json"))
    query = (f"({_pubmed_phrase(pico.population)}) AND ({_pubmed_phrase(pico.intervention)}) "
             f"AND (systematic review[pt] OR meta-analysis[pt])")
    verified = run_async(_verified_prior_reviews(query))
    titles = ("\n".join(f"- PMID {r['pmid']}: {r['title']}" for r in verified)
              or "(no prior systematic reviews found)")
    spec = resolve_reviewer(ctx.cfg, "r1")
    assess = gateway_parse(
        NoveltyAssessment, _NOVELTY_SYSTEM,
        f"Proposed review: {pico.intervention} in {pico.population}\nVerified prior reviews:\n{titles}",
        stage="novelty_check", spec=spec, content_class="metadata", max_tokens=2000)
    report = {
        "query": query,
        "n_prior_reviews": len(verified),
        "prior_reviews": [{**r, "verified": True} for r in verified],   # all live-verified
        "duplication": assess.duplication,
        "gap_statement": assess.gap_statement,
        "recommendation": assess.recommendation,
    }
    path, _ = write_json(run_dir, "novelty_report.json", report)
    return StageResult(
        outputs={"novelty_report.json": path},
        summary={"n_prior_reviews": len(verified), "duplication": assess.duplication,
                 "recommendation": assess.recommendation},
        deterministic_inputs={"pico.json": _hash_of(run_dir, "pico.json")},
    )


# ------------------------------------------------------------------ WP-M3.3 feasibility
class FeasibilityAssessment(BaseModel):
    nma_connected: bool
    network_rationale: str
    recommendation: Literal["proceed", "refine", "stop"]


_FEAS_SYSTEM = (
    "You assess whether a systematic review / NMA is feasible. Given a scoping COUNT of "
    "candidate primary studies and the PICO's comparator structure, judge whether a "
    "CONNECTED comparator network (required for network meta-analysis) is plausible, and "
    "recommend proceed/refine/stop. Reason only from the provided count and comparators — "
    "do NOT invent study counts or citations."
)


def feasibility_scope_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """WP-M3.3 — scope candidate primary-study volume (verified count) + NMA viability."""
    pico = PicoDraft.model_validate(read_json(run_dir, "pico.json"))
    query = (f"({_pubmed_phrase(pico.population)}) AND ({_pubmed_phrase(pico.intervention)}) "
             f"AND (clinical trial[pt] OR cohort studies[mh])")
    records = run_async(_verified_records(query))
    est = len(records)
    spec = resolve_reviewer(ctx.cfg, "r1")
    assess = gateway_parse(
        FeasibilityAssessment, _FEAS_SYSTEM,
        f"PICO: {pico.intervention} vs {pico.comparator or '(single-arm/none)'} in "
        f"{pico.population}. Outcomes: {pico.outcomes}. Verified candidate primary studies "
        f"found (scoping count): {est}.",
        stage="feasibility_scope", spec=spec, content_class="metadata", max_tokens=2000)
    report = {
        "query": query,
        "estimated_primary_studies": est,
        "estimate_method": "verified PubMed scoping count — indicative, NOT an exhaustive search",
        "nma_connected": assess.nma_connected,
        "nma_assessment_method": "LLM advisory plausibility judgment; NOT a verified "
                                 "comparator-network analysis",
        "network_rationale": assess.network_rationale,
        "recommendation": assess.recommendation,
    }
    path, _ = write_json(run_dir, "feasibility_report.json", report)
    return StageResult(
        outputs={"feasibility_report.json": path},
        summary={"estimated_primary_studies": est, "nma_connected": assess.nma_connected,
                 "recommendation": assess.recommendation},
        deterministic_inputs={"pico.json": _hash_of(run_dir, "pico.json")},
    )


# ----------------------------------------------------------- WP-M3.5 autoconfig+promote
def _gen_pico_yaml(pico: PicoDraft, slug: str) -> dict:
    return {
        "review": {"title": f"{pico.intervention} in {pico.population}"[:140],
                   "short_id": slug, "sentinel_strict": False},
        "pico": {"population": pico.population, "intervention": pico.intervention,
                 "comparator": pico.comparator, "outcomes": list(pico.outcomes)},
        "eligibility": {"inclusion": list(pico.inclusion_criteria),
                        "exclusion": list(pico.exclusion_criteria)},
        "synthesis": {},
        "disclaimer": "AI-assisted draft protocol — requires human review (Reviewer 2). "
                      "Not a medical device.",
    }


def _gen_search_yaml(pico: PicoDraft) -> dict:
    q = f"({_pubmed_phrase(pico.population)}) AND ({_pubmed_phrase(pico.intervention)})"
    return {"pubmed": {"query": q}, "named_trials": [], "reference_mining_seeds": [],
            "disclaimer": "Auto-generated search strategy — refine before the live run."}


def _gen_extraction_yaml(pico: PicoDraft) -> dict:
    # fields is a group->[{name, level}] mapping (the shape cfg.extraction_fields() reads)
    items = [{"name": o, "level": "abstract"} for o in pico.outcomes] \
        or [{"name": "primary_outcome", "level": "abstract"}]
    return {"fields": {"efficacy": items}}


def autoconfig_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    """WP-M3.5 — generate a vertical config from the approved PICO under the run dir,
    validated by loading it through SRConfig (the existing config schema)."""
    pico = PicoDraft.model_validate(read_json(run_dir, "pico.json"))
    slug = Path(run_dir).name or "generated"
    gen = Path(run_dir) / "generated_vertical"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "pico.yaml").write_text(yaml.safe_dump(_gen_pico_yaml(pico, slug), sort_keys=True),
                                   encoding="utf-8")
    (gen / "search_strategies.yaml").write_text(yaml.safe_dump(_gen_search_yaml(pico), sort_keys=True),
                                                encoding="utf-8")
    (gen / "extraction_schema.yaml").write_text(yaml.safe_dump(_gen_extraction_yaml(pico), sort_keys=True),
                                                encoding="utf-8")

    # Validate by loading the generated vertical through the real config schema.
    from core.sr.config import SRConfig
    cfg = SRConfig(gen)
    _ = (cfg.review_title, cfg.pico_text(), cfg.inclusion_criteria, cfg.exclusion_criteria)

    path, _h = write_json(run_dir, "autoconfig_summary.json",
                          {"slug": slug, "review_title": cfg.review_title,
                           "files": ["pico.yaml", "search_strategies.yaml", "extraction_schema.yaml"],
                           "validated": True})
    return StageResult(
        outputs={"autoconfig_summary.json": path, "generated_vertical": gen},
        summary={"slug": slug, "review_title": cfg.review_title, "validated": True},
        deterministic_inputs={"pico.json": _hash_of(run_dir, "pico.json")},
    )


def _freeze_hash(gen: Path) -> str:
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(gen.glob("*.yaml"))}
    return hash_json(files)


def promote_protocol(run_dir: Path, slug: str, *, ts_utc: str,
                     verticals_dir: Path = Path("verticals")) -> dict:
    """Human action: copy the generated vertical to verticals/<slug>/ and write a
    PROSPERO-style freeze record (content hash + timestamp). Pre-registration artifact."""
    if not _SLUG_RE.match(slug):
        raise ValueError(f"invalid slug {slug!r} (alphanumeric, '_'/'-', max 64 chars)")
    gen = Path(run_dir) / "generated_vertical"
    if not gen.exists():
        raise FileNotFoundError(f"{gen} (run autoconfig first)")
    base = Path(verticals_dir).resolve()
    dest = (base / slug).resolve()
    if dest != base and base not in dest.parents:    # defense-in-depth vs path traversal
        raise ValueError(f"slug {slug!r} escapes the verticals directory")
    dest.mkdir(parents=True, exist_ok=True)
    for p in gen.glob("*.yaml"):
        shutil.copy(p, dest / p.name)
    freeze = {"slug": slug, "ts_utc": ts_utc, "freeze_hash": _freeze_hash(gen),
              "promoted_to": str(dest)}
    write_json(run_dir, "protocol_freeze.json", freeze)
    return freeze


def record_deviation(run_dir: Path, *, field: str, old, new, ts_utc: str, reason: str = "") -> None:
    """Record a post-freeze protocol change (must surface in the manuscript deviations)."""
    path = Path(run_dir) / "protocol_deviations.json"
    devs = read_json(run_dir, "protocol_deviations.json") if path.exists() else []
    devs.append({"field": field, "old": old, "new": new, "ts_utc": ts_utc, "reason": reason})
    write_json(run_dir, "protocol_deviations.json", devs)
