"""The engine's built-in tools, registered into the registry.

Each tool is a thin wrapper over an existing, tested capability, adapted to the
artifact model: read the run-folder file(s) it needs, do the work, write its own
file. Three are wired here as the first slice (one finder, one AI reader, one math
tool); the rest of the catalog follows the same pattern.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel

from core.models import Citation, ClaimCheck, IdType
from core.tools.registry import register
from core.tools.spec import BackendPolicy, Scorecard, Tool

_BENCH = "memory: project_sr_benchmarking"


def _citation(rec: dict) -> Citation:
    return Citation(id_type=IdType.PMID, id=rec.get("id") or rec["pmid"],
                    source=rec.get("source", "pubmed"),
                    title=rec.get("title", ""), url=rec.get("url", ""),
                    snippet=rec.get("snippet") or rec.get("abstract", ""),
                    study_type=rec.get("study_type", ""),
                    year=rec.get("year"), verified=rec.get("verified", True),
                    doi=rec.get("doi", ""))


# --- search: live literature search -> corpus.json (deterministic, no brain) ---
def _run_search(run_dir: Path, args: dict, backend: str) -> Path:
    import asyncio

    from core.sr.config import load_sr_config
    from core.sr.pipeline import gather_corpus
    from core.sr.agent_io import dump_corpus, records_for_agents

    cfg = load_sr_config(args.get("vertical"))
    corpus = asyncio.run(gather_corpus(cfg, retmax=args.get("retmax")))
    out = run_dir / "corpus.json"
    dump_corpus(corpus, out)
    (run_dir / "records_for_agents.json").write_text(
        json.dumps(records_for_agents(corpus), indent=2), encoding="utf-8")
    return out


# --- screen: include/exclude each study (AI; cloud now, local once earned) ---
def _run_screen(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.agent_io import load_corpus, records_for_agents
    from core.sr.config import load_sr_config
    from core.sr.models import ScreenDecision
    from core.sr.screen import screen_batch
    from core.sr.prescreen import prescreen_records

    cfg = load_sr_config(args.get("vertical"))
    corpus = load_corpus(run_dir / "corpus.json", cfg)
    if backend == "harness":
        from core.tools import harness

        source = harness.harness_source(args)
        # Deterministic pre-screen: drop editorials/letters by type, route conference
        # abstracts to the sensitivity bucket, before any LLM screening (PRISMA 2.2.3/2.2.4).
        items, prescreen_report = prescreen_records(records_for_agents(corpus))
        (run_dir / "prescreen.json").write_text(json.dumps(prescreen_report, indent=2),
                                                encoding="utf-8")
        if not items:
            results = []
        else:
            request, expected = harness.prepare_request(run_dir, "screen", source, cfg, items)
            missing_files = harness.missing_result_files(run_dir, "screen", source, expected)
            if missing_files:
                raise harness.pending(run_dir, "screen", source, request, expected)
            result = harness.collect_results(run_dir, "screen", source,
                                             [str(r["pmid"]) for r in items])
            if result.missing:
                raise harness.pending(run_dir, "screen", source, request, expected)
            results = result.valid
            harness.record_stage(run_dir, "screening", source)
    else:
        # PR10 keeps the proven cloud batched path. Follow-up before local_validated=True:
        # build a per-record local screening path that still recovers dropped PMIDs and
        # drops hallucinated PMIDs with the same guardrails as screen_batch.
        to_screen, prescreen_report = prescreen_records(corpus.verified)
        (run_dir / "prescreen.json").write_text(json.dumps(prescreen_report, indent=2),
                                                encoding="utf-8")
        results = screen_batch(to_screen, cfg) if to_screen else []
    out = run_dir / "screening_merged.json"
    out.write_text(json.dumps([r.model_dump(mode="json") for r in results], indent=2),
                   encoding="utf-8")
    includes = [r.pmid for r in results if r.decision == ScreenDecision.INCLUDE]
    (run_dir / "includes.json").write_text(json.dumps(includes, indent=2), encoding="utf-8")
    return out


def _run_extract(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.config import load_sr_config
    from core.sr.extract import _fill_missing_nr, extract_study

    cfg = load_sr_config(args.get("vertical"))
    records = {
        str(r["pmid"]): r
        for r in json.loads((run_dir / "records_for_agents.json").read_text(encoding="utf-8"))
    }
    includes = [str(p) for p in json.loads((run_dir / "includes.json").read_text(encoding="utf-8"))]
    extractions = []
    if backend == "harness":
        from core.tools import harness

        source = harness.harness_source(args)
        items = [records[p] for p in includes if p in records]
        if items:
            request, expected = harness.prepare_request(run_dir, "extract", source, cfg, items)
            missing_files = harness.missing_result_files(run_dir, "extract", source, expected)
            if missing_files:
                raise harness.pending(run_dir, "extract", source, request, expected)
            result = harness.collect_results(run_dir, "extract", source, [str(r["pmid"]) for r in items])
            if result.missing:
                raise harness.pending(run_dir, "extract", source, request, expected)
            by_pmid = {es.pmid: _fill_missing_nr(es, cfg) for es in result.valid}
            for i, pmid in enumerate(includes):
                if pmid not in by_pmid:
                    continue
                es = by_pmid[pmid]
                es.pmid = pmid
                extractions.append(es)
                (run_dir / f"extract_idx_{i:03d}.json").write_text(
                    es.model_dump_json(indent=2), encoding="utf-8")
            harness.record_stage(run_dir, "extraction", source)
        out = run_dir / "extractions.json"
        out.write_text(json.dumps({"extractions": [e.model_dump(mode="json") for e in extractions]},
                                  indent=2), encoding="utf-8")
        return out
    for i, pmid in enumerate(includes):
        if pmid not in records:
            continue
        es = _fill_missing_nr(extract_study(_citation(records[pmid]), cfg), cfg)
        es.pmid = pmid
        extractions.append(es)
        (run_dir / f"extract_idx_{i:03d}.json").write_text(
            es.model_dump_json(indent=2), encoding="utf-8")
    out = run_dir / "extractions.json"
    out.write_text(json.dumps({"extractions": [e.model_dump(mode="json") for e in extractions]},
                              indent=2), encoding="utf-8")
    return out


def _extraction_items(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("extractions", "studies"):
            if key in raw:
                return raw[key]
    raise ValueError("extractions must be a list or contain 'extractions'/'studies'")


def _modality_by_pmid(run_dir: Path, subgroup_field: str = "") -> dict[str, str]:
    p = run_dir / "screening_merged.json"
    if not p.exists():
        return {}
    from core.sr.agent_io import load_screening
    results = load_screening(json.loads(p.read_text(encoding="utf-8")))
    return {s.pmid: str(getattr(s, subgroup_field, "") or s.modality or "") for s in results}


def _load_extractions(run_dir: Path):
    from core.tools.sr_io import load_tool_extractions
    return load_tool_extractions(run_dir)


def _select_pool_spec(specs: list[dict], field: str | None, kind: str) -> dict:
    if not specs:
        raise ValueError(f"no {kind} fields configured for this vertical")
    if field:
        for spec in specs:
            if spec.get("field") == field:
                return spec
        raise ValueError(f"{kind} field not configured: {field}")
    if len(specs) > 1:
        names = ", ".join(str(s.get("field")) for s in specs)
        raise ValueError(f"choose one {kind} field with --field ({names})")
    return specs[0]


def _beneficial(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "yes", "1"}:
            return True
        if low in {"false", "no", "0"}:
            return False
    return None


def _round_or_none(value, ndigits=3):
    return None if value is None else round(value, ndigits)


def _number_needed_payload(nn) -> dict:
    return {
        "label": nn.label,
        "value": _round_or_none(nn.value, 1),
        "ci": None if nn.ci_lb is None or nn.ci_ub is None else [
            _round_or_none(nn.ci_lb, 1), _round_or_none(nn.ci_ub, 1)],
        "direction": nn.direction,
        "note": nn.note,
    }


def _imputation_counts(rows) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        method = getattr(row, "imputation_method", "")
        if getattr(row, "imputed", False):
            counts[method or "unspecified"] = counts.get(method or "unspecified", 0) + 1
    return counts


def _select_proportion_field(cfg, requested: str | None) -> tuple[str, str]:
    fields = cfg.poolable_fields().get("proportion", [])
    labels = dict(fields)
    if requested:
        return requested, labels.get(requested, requested)
    if not fields:
        raise ValueError("no configured proportion outcomes; pass --field <name>")
    return fields[0]


# --- pool-proportions: combine response/event rates (deterministic math, local) ---
def _run_pool_proportions(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.config import load_sr_config
    from core.sr.stats.meta import r_available, run_metaprop
    from core.sr.stats.prepare import outcome_rows

    cfg = load_sr_config(args.get("vertical"))
    field, label = _select_proportion_field(cfg, args.get("field"))
    studies = _load_extractions(run_dir)
    rows, excluded = outcome_rows(studies, field, _modality_by_pmid(run_dir, cfg.subgroup_field),
                                  subgroup_field=cfg.subgroup_field,
                                  subgroup_map=cfg.subgroup_map)
    out_payload: dict = {"field": field, "n_pooled": len(rows), "n_excluded": excluded,
                         "available": False, "results": []}
    if r_available() and len(rows) >= 2:
        res = run_metaprop(rows, label, run_dir / f"forest_{field}.png")
        out_payload["available"] = True
        out_payload["results"] = [{"group": r.group, "k": r.k, "prop": round(r.prop, 1),
                                   "ci": [round(r.ci_lb, 1), round(r.ci_ub, 1)],
                                   "I2": round(r.I2, 0)} for r in res]
    else:
        out_payload["note"] = "need R/metafor and >=2 poolable studies"
    out = run_dir / "stats_proportions.json"
    out.write_text(json.dumps(out_payload, indent=1))
    return out


def _run_metabin_tool(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.config import load_sr_config
    from core.sr.stats import meta
    from core.sr.stats.prepare import bin_rows

    cfg = load_sr_config(args.get("vertical"))
    spec = _select_pool_spec(cfg.binary_2x2_fields(), args.get("field"), "binary_2x2")
    studies = _load_extractions(run_dir)
    rows, excluded = bin_rows(studies, spec["pair"], _modality_by_pmid(run_dir, cfg.subgroup_field),
                              subgroup_field=cfg.subgroup_field,
                              subgroup_map=cfg.subgroup_map)
    measure = args.get("measure") or "RR"
    method = args.get("method") or "MH"
    out_payload: dict = {
        "field": spec["field"],
        "label": spec["label"],
        "beneficial": spec.get("beneficial"),
        "measure": measure,
        "method": method,
        "n_pooled": len(rows),
        "n_excluded": excluded,
        "available": False,
        "results": [],
    }
    if meta.metabin_available() and len(rows) >= 2:
        res = meta.run_metabin(rows, spec["label"], run_dir / f"forest_{spec['field']}.png",
                               measure=measure, method=method,
                               beneficial=_beneficial(spec.get("beneficial")))
        out_payload["available"] = True
        out_payload["results"] = [{
            "group": r.group, "k": r.k, "measure": r.measure,
            "estimate": _round_or_none(r.estimate),
            "ci": [_round_or_none(r.ci_lb), _round_or_none(r.ci_ub)],
            "risk_difference": _round_or_none(r.rd),
            "risk_difference_ci": None if r.rd_lb is None or r.rd_ub is None else [
                _round_or_none(r.rd_lb), _round_or_none(r.rd_ub)],
            "number_needed": _number_needed_payload(r.number_needed),
            "Q": _round_or_none(r.Q),
            "I2": _round_or_none(r.I2, 1),
            "I2_ci": None if r.I2_lb is None or r.I2_ub is None else [
                _round_or_none(r.I2_lb, 1), _round_or_none(r.I2_ub, 1)],
            "tau2": _round_or_none(r.tau2),
            "prediction_interval": None if r.pi_lb is None or r.pi_ub is None else [
                _round_or_none(r.pi_lb), _round_or_none(r.pi_ub)],
        } for r in res]
    else:
        out_payload["note"] = "need R/meta and >=2 poolable 2x2 studies"
    out = run_dir / "stats_binary.json"
    out.write_text(json.dumps(out_payload, indent=1), encoding="utf-8")
    return out


def _run_metacont_tool(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.config import load_sr_config
    from core.sr.stats import meta
    from core.sr.stats.prepare import cont_rows

    cfg = load_sr_config(args.get("vertical"))
    spec = _select_pool_spec(cfg.continuous_fields(), args.get("field"), "continuous")
    studies = _load_extractions(run_dir)
    rows, excluded = cont_rows(studies, spec["pair"], _modality_by_pmid(run_dir, cfg.subgroup_field),
                               subgroup_field=cfg.subgroup_field,
                               subgroup_map=cfg.subgroup_map)
    measure = args.get("measure") or "SMD"
    out_payload: dict = {
        "field": spec["field"],
        "label": spec["label"],
        "measure": measure,
        "n_pooled": len(rows),
        "n_excluded": excluded,
        "n_imputed": sum(1 for r in rows if r.imputed),
        "imputation_methods": _imputation_counts(rows),
        "available": False,
        "results": [],
    }
    if meta.metacont_available() and len(rows) >= 2:
        res = meta.run_metacont(rows, spec["label"], run_dir / f"forest_{spec['field']}.png",
                                measure=measure)
        out_payload["available"] = True
        out_payload["results"] = [{
            "group": r.group, "k": r.k, "measure": r.measure,
            "estimate": _round_or_none(r.estimate),
            "ci": [_round_or_none(r.ci_lb), _round_or_none(r.ci_ub)],
            "Q": _round_or_none(r.Q),
            "I2": _round_or_none(r.I2, 1),
            "I2_ci": None if r.I2_lb is None or r.I2_ub is None else [
                _round_or_none(r.I2_lb, 1), _round_or_none(r.I2_ub, 1)],
            "tau2": _round_or_none(r.tau2),
            "prediction_interval": None if r.pi_lb is None or r.pi_ub is None else [
                _round_or_none(r.pi_lb), _round_or_none(r.pi_ub)],
        } for r in res]
    else:
        out_payload["note"] = "need R/meta and >=2 poolable continuous studies"
    out = run_dir / "stats_continuous.json"
    out.write_text(json.dumps(out_payload, indent=1), encoding="utf-8")
    return out


def _select_metareg_outcome(cfg, outcome: str) -> dict:
    log_labels = dict(cfg.poolable_fields().get("log_effect", []))
    if outcome in log_labels:
        return {"kind": "log_effect", "field": outcome, "label": log_labels[outcome], "pair": None}
    for spec in cfg.continuous_fields():
        if spec.get("field") == outcome:
            return {"kind": "continuous", **spec}
    raise ValueError(f"meta-regression outcome not configured as log_effect or continuous: {outcome}")


def _metareg_result_payload(result) -> dict:
    return {
        "available": result.available,
        "reliable": result.reliable,
        "note": result.note,
        "k": result.k,
        "terms": [{
            "term": t.term,
            "estimate": _round_or_none(t.estimate, 6),
            "se": _round_or_none(t.se, 6),
            "ci": [_round_or_none(t.ci_lb, 6), _round_or_none(t.ci_ub, 6)],
            "pval": _round_or_none(t.pval, 6),
        } for t in result.terms],
        "QM": _round_or_none(result.QM, 6),
        "QM_p": _round_or_none(result.QM_p, 6),
        "QE": _round_or_none(result.QE, 6),
        "QE_p": _round_or_none(result.QE_p, 6),
        "I2_resid": _round_or_none(result.I2_resid, 6),
        "R2": _round_or_none(result.R2, 6),
    }


def _run_meta_regression_tool(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.config import load_sr_config
    from core.sr.stats import meta
    from core.sr.stats.prepare import metareg_rows

    outcome = args.get("outcome")
    moderator = args.get("moderator")
    if not outcome or not moderator:
        raise ValueError("meta-regression requires --outcome <field> and --moderator <field>")
    cfg = load_sr_config(args.get("vertical"))
    spec = _select_metareg_outcome(cfg, outcome)
    studies = _load_extractions(run_dir)
    rows, excluded = metareg_rows(
        studies, outcome, moderator, _modality_by_pmid(run_dir, cfg.subgroup_field),
        outcome_kind=spec["kind"], continuous_pair=spec.get("pair"),
        subgroup_field=cfg.subgroup_field, subgroup_map=cfg.subgroup_map)
    min_k = int(args.get("min_k") or 10)
    out_payload = {
        "outcome": outcome,
        "moderator": moderator,
        "n_pooled": len(rows),
        "n_excluded": excluded,
        "available": False,
        "reliable": False,
        "results": {},
    }
    if len(rows) < 4:
        out_payload["note"] = "insufficient studies for meta-regression (k<4)"
    elif not meta.metareg_available():
        out_payload["note"] = "need R/metafor and >=4 meta-regression rows"
    else:
        result = meta.run_metareg(rows, moderator, min_k=min_k)
        out_payload.update(_metareg_result_payload(result))
    out = run_dir / "stats_metareg.json"
    out.write_text(json.dumps(out_payload, indent=1), encoding="utf-8")
    return out


def _run_fulltext(run_dir: Path, args: dict, backend: str) -> Path:
    """Legal OA coverage/overlay wrapper.

    Follow-up tracked for the PR description, not implemented here: fulltext-aware
    extraction so this tool-based review can extract directly from full text.
    """
    from core.sr.fetch_oa import harvest_oa, overlay_fulltext, write_report
    from core.tools.sr_io import load_records, load_tool_extractions, write_extractions_list

    includes = [str(p) for p in json.loads((run_dir / "includes.json").read_text(encoding="utf-8"))]
    records = load_records(run_dir)
    dest = Path(args.get("dest") or (run_dir / "fulltext")).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    doi_map = {p: records.get(p, {}).get("doi", "") for p in includes}
    results = [] if args.get("overlay_only") else asyncio.run(
        harvest_oa(includes, doi_map, dest, concurrency=int(args.get("concurrency") or 4)))
    report = {}
    if not args.get("overlay_only"):
        report = json.loads(write_report(results, dest).read_text(encoding="utf-8"))

    overlay = {"updated_fields": [], "n": 0}
    if list(run_dir.glob("fulltext_*.json")):
        write_extractions_list(run_dir, "extractions_merged.json", load_tool_extractions(run_dir))
        overlay = overlay_fulltext(run_dir)
    report["overlay"] = overlay
    report["note"] = ("Fulltext-aware extraction is a tracked follow-up; this tool performs "
                      "legal OA coverage plus NR-only overlay when fulltext_<pmid>.json exists.")
    out = run_dir / "fulltext_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def _study_text(record: dict, extraction=None) -> str:
    lines = [record.get("title", ""), record.get("abstract", "")]
    if extraction is not None:
        for v in extraction.values:
            if v.quote:
                lines.append(f"{v.field}: {v.value}\nQUOTE: {v.quote}")
            elif v.value and v.value.upper() != "NR":
                lines.append(f"{v.field}: {v.value}")
    return "\n\n".join(x for x in lines if x)


def _run_risk_of_bias(run_dir: Path, args: dict, backend: str) -> Path:
    from core.llm import client as llm
    from core.sr.models import RoBAssessment
    from core.sr.rob import build_prompt, normalize
    from core.tools.sr_io import load_records, load_tool_extractions, load_tool_screening

    records = load_records(run_dir)
    screening = {s.pmid: s for s in load_tool_screening(run_dir)}
    assessments = []
    for i, es in enumerate(load_tool_extractions(run_dir)):
        s = screening.get(es.pmid)
        design = s.study_design if s else ""
        _, system, user = build_prompt(es.pmid, design, _study_text(records.get(es.pmid, {}), es))
        assessment = normalize(llm.parse(RoBAssessment, system, user, stage="risk_of_bias"))
        assessment.pmid = es.pmid
        assessments.append(assessment)
        (run_dir / f"rob_idx_{i:03d}.json").write_text(
            assessment.model_dump_json(indent=2), encoding="utf-8")
    out = run_dir / "rob.json"
    out.write_text(json.dumps({"assessments": [a.model_dump(mode="json") for a in assessments]},
                              indent=2), encoding="utf-8")
    return out


def _run_grade(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.grade import grade_outcomes
    from core.sr.signoff import DRAFT_CERTAINTY, is_locked
    from core.tools.sr_io import load_rob, load_run_bundle

    bundle = load_run_bundle(run_dir, args.get("vertical"))
    rows = grade_outcomes(bundle.run.extractions, bundle.run.screening, load_rob(run_dir))
    locked = is_locked(bundle.run)
    payload = {
        "locked": locked,
        "reviewer_signoff": None if bundle.run.reviewer_signoff is None
        else bundle.run.reviewer_signoff.model_dump(mode="json"),
        "rows": [g.model_dump(mode="json") for g in rows],
        "rendered_rows": [
            g.model_dump(mode="json") | {
                "rendered_certainty": DRAFT_CERTAINTY if locked else g.certainty}
            for g in rows
        ],
    }
    out = run_dir / "grade.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


class _NarrativeDraft(BaseModel):
    abstract_background: str = ""
    abstract_results: str = ""
    abstract_conclusions: str = ""
    introduction: str = ""
    discussion: str = ""


# Support-check hardening (Decision 2 / gap G5). Research flags single-shot
# LLM-as-judge recall at 16–17%, so a lone judgment is not trusted: each sentence is
# checked k times from distinct adversarial lenses and kept only on a ≥ min-of-k
# majority. An errored ballot counts as unsupported (fail closed).
_SUPPORT_VOTES = 3
_SUPPORT_MIN = 2
_SUPPORT_LENSES = (
    "Decide strictly whether the cited source text DIRECTLY supports the claim. Any "
    "quantitative detail not stated verbatim in the sources means NOT supported.",
    "You are an adversarial reviewer trying to REFUTE the claim. Mark supported=true "
    "only if refutation is impossible given the sources alone.",
    "Test logical entailment: do the sources entail the claim with no outside knowledge? "
    "Topical or partial overlap is NOT support.",
)


_PMID_CITED_RE = re.compile(r"\[PMID:\s*([\d,\s]+?)\]", re.I)


def _cited_pmids(sentence: str) -> list[str]:
    """PMIDs a sentence actually cites, incl. comma lists ``[PMID: 111, 222]``. A
    malformed token (``[PMID: abc]``) yields none, so the sentence is treated as uncited."""
    out: list[str] = []
    for m in _PMID_CITED_RE.finditer(sentence):
        for tok in m.group(1).split(","):
            tok = tok.strip()
            if tok.isdigit() and tok not in out:
                out.append(tok)
    return out


def _quotes_by_pmid(run_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    try:
        from core.tools.sr_io import load_tool_extractions
        for es in load_tool_extractions(run_dir):
            for v in es.values:
                if v.quote:
                    out.setdefault(es.pmid, []).append(v.quote)
    except Exception:
        pass
    return out


def _support_sources_for(records: dict[str, dict], pmids: list[str],
                         quotes_by_pmid: dict[str, list[str]]) -> str:
    """Grounded pool scoped to the CITED studies only: their abstract + their own
    extracted quotes. A claim must be supported by the study it cites, not by any study
    in the corpus — so the pool is per-citation. Reference *titles* are excluded: a title
    that asserts a conclusion ("Drug X improves PFS") is not evidence for a claim."""
    parts: list[str] = []
    for pmid in pmids:
        ab = (records.get(pmid, {}) or {}).get("abstract", "")
        if ab:
            parts.append(f"[PMID: {pmid}] {ab[:1500]}")
        for q in quotes_by_pmid.get(pmid, []):
            parts.append(f"[PMID: {pmid}] (extracted) {q}")
    return "\n\n".join(parts)


def _claim_support_check(text: str, sources: str, *, votes: int = _SUPPORT_VOTES,
                         min_supported: int = _SUPPORT_MIN) -> dict:
    """k-of-n multi-vote entailment check. Returns the aggregated verdict plus every
    ballot so the per-sentence provenance is auditable."""
    from core.llm import client as llm

    user = f"<claim>\n{text}\n</claim>\n\n<sources>\n{sources}\n</sources>"
    ballots: list[bool] = []
    notes: list[str] = []
    for i in range(votes):
        system = ("You are a strict systematic-review fact-checker. "
                  + _SUPPORT_LENSES[i % len(_SUPPORT_LENSES)])
        try:
            check = llm.parse(ClaimCheck, system, user, stage="citation_support")
            ballots.append(bool(check.supported))
            if check.notes:
                notes.append(check.notes)
        except Exception:
            ballots.append(False)   # fail closed: an errored ballot is not support
    n_supported = sum(ballots)
    return {
        "supported": n_supported >= min_supported,
        "votes": ballots,
        "n_supported": n_supported,
        "n_votes": votes,
        "min_required": min_supported,
        "notes": " | ".join(notes[:2]),
    }


_NARRATIVE_CLAIM_FIELDS = (
    "abstract_background", "abstract_results", "abstract_conclusions",
    "introduction", "discussion",
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split((text or "").strip()) if s.strip()]


def _fallback_narrative() -> dict[str, str]:
    msg = ("Abstract-level evidence did not support AI-drafted narrative prose after "
           "strict claim-check; unsupported claims were dropped for human review.")
    return {
        "abstract_background": msg,
        "abstract_results": msg,
        "abstract_conclusions": msg,
        "introduction": msg,
        "discussion": msg,
    }


def _filter_narrative_claims(
    narrative: dict[str, str],
    run_dir: Path,
    records: dict[str, dict],
) -> tuple[dict[str, str], list[dict], list[dict], dict[str, int]]:
    checked: list[dict] = []
    dropped: list[dict] = []
    metrics = {
        "sentences_considered": 0,
        "kept": 0,
        "dropped_no_citation": 0,
        "claim_checked": 0,
        "dropped_unsupported": 0,
        "dropped_total": 0,
    }
    cleaned = dict(narrative)
    quotes_by_pmid = _quotes_by_pmid(run_dir)      # grouped once; scoped per-sentence below
    for field in _NARRATIVE_CLAIM_FIELDS:
        kept: list[str] = []
        for sentence in _split_sentences(narrative.get(field, "")):
            metrics["sentences_considered"] += 1
            cited = _cited_pmids(sentence)
            if not cited:
                # No valid citation (missing or malformed) — a substantive sentence must
                # cite a study. Applies regardless of length (a short "PFS doubled" claim
                # is still a claim), so nothing bypasses the citation requirement.
                metrics["dropped_no_citation"] += 1
                dropped.append({"field": field, "sentence": sentence,
                                "reason": "missing/malformed PMID citation"})
                continue
            # Scope the pool to the CITED study/studies: "supported" now means supported
            # by the source the sentence cites, not by any study in the corpus.
            sources = _support_sources_for(records, cited, quotes_by_pmid)
            check = _claim_support_check(sentence, sources)
            metrics["claim_checked"] += 1
            checked.append({"field": field, "sentence": sentence, "cited": cited,
                            "claim_check": check})
            if check.get("supported", False):
                kept.append(sentence)
                metrics["kept"] += 1
            else:
                metrics["dropped_unsupported"] += 1
                dropped.append({
                    "field": field,
                    "sentence": sentence,
                    "reason": check.get("notes", "unsupported"),
                })
        cleaned[field] = " ".join(kept)
    metrics["dropped_total"] = metrics["dropped_no_citation"] + metrics["dropped_unsupported"]
    if not any((cleaned.get(field) or "").strip() for field in _NARRATIVE_CLAIM_FIELDS):
        cleaned = _fallback_narrative()
    return cleaned, dropped, checked, metrics


def _run_narrative(run_dir: Path, args: dict, backend: str) -> Path:
    from core.llm import client as llm
    from core.sr.narrative import build_evidence_brief, validate_citations, valid_citation_pmids
    from core.sr.signoff import DRAFT_BANNER, is_locked
    from core.tools.sr_io import load_grade, load_rob, load_run_bundle, load_stats

    bundle = load_run_bundle(run_dir, args.get("vertical"))
    rob = load_rob(run_dir)
    grade = load_grade(run_dir)
    stats = load_stats(run_dir)
    included = [
        {"pmid": pmid, **bundle.records.get(pmid, {})}
        for pmid in json.loads((run_dir / "includes.json").read_text(encoding="utf-8"))
    ]
    brief = build_evidence_brief(bundle.run, bundle.cfg, stats, grade, rob, [], included)
    system = (
        "You draft conservative systematic-review manuscript prose only from the supplied "
        "grounded evidence brief. Write only factual statements about included studies that "
        "are directly supported by the brief. Cite included studies as [PMID: n] in every "
        "substantive sentence. Do not add outside facts. Do not write review-level synthesis "
        "conclusions, recommendations, certainty judgments, GRADE statements, or risk-of-bias "
        "claims; those are added downstream under the conclusion lock. If support is thin, "
        "omit the claim rather than speculating."
    )
    draft = llm.parse(_NarrativeDraft, system, brief, stage="narrative")
    narrative = draft.model_dump(mode="json")
    valid, invalid = validate_citations(" ".join(narrative.values()),
                                        valid_citation_pmids(bundle.run, []))
    if invalid:
        raise ValueError(f"narrative cited invalid PMIDs: {invalid}")
    included_records = {str(s["pmid"]): bundle.records.get(str(s["pmid"]), {}) for s in included}
    cleaned, dropped_claims, checked_claims, claim_metrics = _filter_narrative_claims(
        narrative, run_dir, included_records)
    valid, _ = validate_citations(" ".join(cleaned.values()), valid_citation_pmids(bundle.run, []))
    locked = is_locked(bundle.run)
    final = dict(cleaned)
    if locked:
        final["abstract_conclusions"] = DRAFT_BANNER
    (run_dir / "narrative_final.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    out = run_dir / "narrative.json"
    out.write_text(json.dumps({
        "locked": locked,
        "brief": brief,
        "draft": narrative,
        "cleaned": cleaned,
        "final": final,
        "cited_valid": valid,
        "claim_check": {
            "mode": "sentence_filter",
            "check": "multi_vote_entailment",
            "stage": "citation_support",
            "votes_total": _SUPPORT_VOTES,
            "votes_required": _SUPPORT_MIN,
            **claim_metrics,
            "supported": claim_metrics["dropped_total"] == 0,
        },
        "checked_claims": checked_claims,
        "dropped_claims": dropped_claims,
        "stats_source": "stats_configured.json" if (run_dir / "stats_configured.json").exists()
        else "fallback",
    }, indent=2), encoding="utf-8")
    return out


def _run_references(run_dir: Path, args: dict, backend: str) -> Path:
    """Populate the verified, keyed reference library (references.json) from the included
    studies, arming the opt-in reference-integrity gate. Running this is a deliberate
    act, so runs that never call it stay ungated/dormant."""
    from core.sr.references_build import write_references

    return write_references(run_dir)


def _run_manuscript(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.audit import audit_quote_anchoring
    from core.sr.manuscript.export import export_project
    from core.sr.profile import load_profile
    from core.tools.sr_io import load_grade, load_rob, load_run_bundle, load_stats, modality_by_pmid

    # Export gate (orchestrator honesty gates §7a/§7b) — the registered-tool render
    # path must fail closed exactly like `python -m core.sr.manuscript`. None for
    # legacy/manual runs (no run_state.json); (False, reasons) refuses before any work.
    from core.sr.orchestrator.engine import export_gate
    gate = export_gate(run_dir)
    if gate is not None and gate[0] is False:
        # Purge any prior rendered dossier + write the block notice, then fail closed.
        from core.sr.config import load_sr_config
        from core.sr.manuscript.export import write_blocked_export
        write_blocked_export(load_sr_config(args.get("vertical")), gate[1],
                             base_dir=Path(args["base_dir"]) if args.get("base_dir") else None,
                             day=args.get("day"), review_type=load_profile(args.get("profile")).name)
        raise RuntimeError("manuscript export blocked — run has not cleared its honesty "
                           "gates: " + "; ".join(gate[1]))

    bundle = load_run_bundle(run_dir, args.get("vertical"))
    narrative = None
    references = None
    nar = run_dir / "narrative_final.json"
    if nar.exists():
        narrative = json.loads(nar.read_text(encoding="utf-8"))
        # Reference-integrity gate (WP7): fail closed if a key-based references.json is
        # present and the prose cites a broken/unverified reference (same as the
        # `python -m core.sr.manuscript` path; this is the registered-tool path). Prose
        # is normalized ([PMID: n] -> [PMID<n>]) so the gate keys line up.
        from core.sr.references import gate_run_dir, load_library
        from core.sr.references_build import normalize_pmid_citations
        gate_run_dir(run_dir, normalize_pmid_citations(
            " ".join(str(v) for v in narrative.values())))
        ref_path = run_dir / "references.json"
        if ref_path.exists():
            references = load_library(ref_path)
    audit_path = run_dir / "honesty_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {
        "anchoring": audit_quote_anchoring(
            {p: r.get("abstract", "") for p, r in bundle.records.items()}, bundle.run.extractions)
    }
    result = export_project(
        bundle.run, bundle.cfg, bundle.records, audit=audit, rob=load_rob(run_dir),
        grade=load_grade(run_dir), modality_by_pmid=modality_by_pmid(run_dir, bundle.cfg.subgroup_field),
        narrative=narrative, references=references, stats=load_stats(run_dir),
        profile=load_profile(args.get("profile")),
        base_dir=Path(args["base_dir"]) if args.get("base_dir") else None,
        day=args.get("day"), gate=gate)
    payload = result | {
        "locked": bundle.run.reviewer_signoff is None,
        "reviewer_signoff": None if bundle.run.reviewer_signoff is None
        else bundle.run.reviewer_signoff.model_dump(mode="json"),
        "stats_source": "stats_configured.json" if (run_dir / "stats_configured.json").exists()
        else "fallback",
        "stats_available": bool(getattr(load_stats(run_dir), "available", False)),
    }
    out = run_dir / "manuscript.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _run_pool_configured(run_dir: Path, args: dict, backend: str) -> Path:
    from core.sr.config import load_sr_config
    from core.sr.stats.run import meta_analyze
    from core.tools.sr_io import load_tool_extractions, modality_by_pmid

    cfg = load_sr_config(args.get("vertical"))
    stats = meta_analyze(load_tool_extractions(run_dir, cfg), modality_by_pmid(run_dir, cfg.subgroup_field),
                         run_dir, cfg=cfg)

    def obj(value):
        if hasattr(value, "__dataclass_fields__"):
            return {k: obj(getattr(value, k)) for k in value.__dataclass_fields__}
        if isinstance(value, list):
            return [obj(v) for v in value]
        return value

    out = run_dir / "stats_configured.json"
    out.write_text(json.dumps(obj(stats), indent=2), encoding="utf-8")
    return out


def _run_quicklook_summary(run_dir: Path, args: dict, backend: str) -> Path:
    from core.tools.sr_io import load_stats

    corpus = json.loads((run_dir / "corpus.json").read_text(encoding="utf-8"))
    includes = json.loads((run_dir / "includes.json").read_text(encoding="utf-8"))
    stats = load_stats(run_dir)
    n_top = args.get("retmax") or len(corpus.get("verified", []))
    warning = (f"Rapid scan of top {n_top} of {len(corpus.get('identified', []))} records - "
               "NOT a complete review; run Full Review before clinical or publication conclusions.")
    payload = {
        "triage_only": True,
        "warning": warning,
        "n_included": len(includes),
        "stats_available": bool(getattr(stats, "available", False)),
        "final_conclusion": None,
        "certainty": None,
    }
    out = run_dir / "quicklook_summary.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


register(Tool(
    name="search", description="Live literature search for a question -> verified study list.",
    kind="deterministic", input_artifacts=(), output_artifact="corpus.json", run=_run_search,
    args_help="--vertical <name> [--retmax N]",
    scorecard=Scorecard(internally_tested=True, benchmark_ref=_BENCH)))

register(Tool(
    name="screen", description="Decide include/exclude/uncertain for each study (Reviewer 1).",
    kind="ai", input_artifacts=("corpus.json",), output_artifact="screening_merged.json",
    run=_run_screen,
    backend=BackendPolicy(default="cloud", local_validated=False, harness_enabled=True),
    args_help="--vertical <name> [--local|--cloud|--harness] [--harness-source claude|gpt]",
    scorecard=Scorecard(internally_tested=True, benchmark_ref=_BENCH)))

register(Tool(
    name="extract", description="Extract review data fields from included studies.",
    kind="ai", input_artifacts=("records_for_agents.json", "screening_merged.json", "includes.json"),
    output_artifact="extractions.json", run=_run_extract,
    backend=BackendPolicy(default="cloud", local_validated=False, harness_enabled=True),
    args_help="--vertical <name> [--local|--cloud|--harness] [--harness-source claude|gpt]",
    scorecard=Scorecard(internally_tested=True, benchmark_ref=_BENCH)))

register(Tool(
    name="fulltext",
    description="Harvest legal OA full text and overlay available full-text extraction fields.",
    kind="deterministic", input_artifacts=("includes.json", "records_for_agents.json", "extractions.json"),
    output_artifact="fulltext_report.json", run=_run_fulltext,
    args_help="[--dest <path>] [--overlay-only] [--concurrency N]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="risk-of-bias",
    description="Assess risk of bias for included studies (Reviewer-1 draft).",
    kind="ai", input_artifacts=("records_for_agents.json", "screening_merged.json", "extractions.json"),
    output_artifact="rob.json", run=_run_risk_of_bias,
    backend=BackendPolicy(default="cloud", local_validated=False),
    args_help="[--vertical <name>] [--local|--cloud]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="grade",
    description="Compute lock-aware GRADE certainty rows.",
    kind="deterministic", input_artifacts=("corpus.json", "screening_merged.json", "extractions.json"),
    output_artifact="grade.json", run=_run_grade,
    args_help="[--vertical <name>] [--approve-conclusions <reviewer>]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False),
    conclusion_bearing=True))

register(Tool(
    name="pool-proportions",
    description="Combine response/event rates across studies (random-effects).",
    kind="deterministic", input_artifacts=("extractions.json",),
    output_artifact="stats_proportions.json", run=_run_pool_proportions,
    args_help="[--field <name>]",
    scorecard=Scorecard(internally_tested=True, externally_validated=True, benchmark_ref=_BENCH)))

register(Tool(
    name="metabin",
    description="Combine 2x2 binary outcomes across studies (random-effects).",
    kind="deterministic", input_artifacts=("extractions.json",),
    output_artifact="stats_binary.json", run=_run_metabin_tool,
    args_help="[--field <name>] [--measure RR|OR|RD] [--method MH]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="metacont",
    description="Combine continuous two-arm outcomes across studies (random-effects).",
    kind="deterministic", input_artifacts=("extractions.json",),
    output_artifact="stats_continuous.json", run=_run_metacont_tool,
    args_help="[--field <name>] [--measure SMD|MD]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="meta-regression",
    description="Explore heterogeneity by a study-level moderator.",
    kind="deterministic", input_artifacts=("extractions.json",),
    output_artifact="stats_metareg.json", run=_run_meta_regression_tool,
    args_help="--outcome <field> --moderator <field> [--min-k N]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="pool-configured",
    description="Run all configured meta-analysis pools without overwriting single-outcome artifacts.",
    kind="deterministic", input_artifacts=("extractions.json",),
    output_artifact="stats_configured.json", run=_run_pool_configured,
    args_help="[--vertical <name>]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="narrative",
    description="Draft citation-checked narrative prose from grounded SR artifacts.",
    kind="ai", input_artifacts=("corpus.json", "screening_merged.json", "extractions.json",
                                "grade.json", "rob.json"),
    output_artifact="narrative.json", run=_run_narrative,
    backend=BackendPolicy(default="cloud", local_validated=False),
    args_help="[--vertical <name>] [--local|--cloud] [--approve-conclusions <reviewer>]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False),
    conclusion_bearing=True))

register(Tool(
    name="manuscript",
    description="Export the lock-aware manuscript package for an SR run.",
    kind="deterministic", input_artifacts=("corpus.json", "screening_merged.json", "extractions.json"),
    output_artifact="manuscript.json", run=_run_manuscript,
    args_help="[--vertical <name>] [--profile <name>] [--base-dir <path>] [--day YYYY-MM-DD] "
              "[--approve-conclusions <reviewer>]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False),
    conclusion_bearing=True))

register(Tool(
    name="references",
    description="Build the verified, keyed reference library (arms the citation gate).",
    kind="deterministic", input_artifacts=("records_for_agents.json",),
    output_artifact="references.json", run=_run_references,
    args_help="[--vertical <name>]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))

register(Tool(
    name="quick-look-summary",
    description="Write a rapid-scan summary that cannot finalize conclusions.",
    kind="deterministic", input_artifacts=("corpus.json", "includes.json", "stats_configured.json"),
    output_artifact="quicklook_summary.json", run=_run_quicklook_summary,
    args_help="[--retmax N]",
    scorecard=Scorecard(internally_tested=True, externally_validated=False)))
