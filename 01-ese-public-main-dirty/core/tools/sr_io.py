"""Shared SR run-folder loading helpers for tool wrappers.

The tools are thin run-folder adapters, so they need a single place to rebuild
the same SRRun shape the manuscript path already expects. This module keeps
artifact precedence and the reviewer-signoff lock consistent across tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.sr.agent_io import load_corpus, load_extractions, load_screening
from core.sr.config import load_sr_config
from core.sr.extract import _fill_missing_nr
from core.sr.models import ExtractedStudy, GradeRow, ReviewerSignoff, RoBAssessment
from core.sr.pipeline import build_run

SIGNOFF_ARTIFACT = "reviewer_signoff.json"


def extraction_items(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("extractions", "studies"):
            if key in raw:
                return raw[key]
    raise ValueError("extractions must be a list or contain 'extractions'/'studies'")


def load_records(run_dir: Path) -> dict[str, dict]:
    rows = json.loads((run_dir / "records_for_agents.json").read_text(encoding="utf-8"))
    return {str(r["pmid"]): r for r in rows}


def load_tool_extractions(run_dir: Path, cfg=None) -> list[ExtractedStudy]:
    cfg = cfg or load_sr_config()
    src = run_dir / "extractions_merged.json"
    if not src.exists():
        src = run_dir / "extractions.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    return [_fill_missing_nr(es, cfg) for es in load_extractions(extraction_items(raw))]


def write_extractions_list(run_dir: Path, name: str, studies: list[ExtractedStudy]) -> Path:
    out = run_dir / name
    out.write_text(json.dumps([s.model_dump(mode="json") for s in studies], indent=2),
                   encoding="utf-8")
    return out


def load_tool_screening(run_dir: Path):
    for name in ("screening.json", "screening_merged.json"):
        p = run_dir / name
        if p.exists():
            return load_screening(json.loads(p.read_text(encoding="utf-8")))
    raise FileNotFoundError(f"no screening artifact in {run_dir}")


def modality_by_pmid(run_dir: Path) -> dict[str, str]:
    for name in ("screening.json", "screening_merged.json"):
        p = run_dir / name
        if p.exists():
            return {s.pmid: s.modality for s in load_screening(json.loads(p.read_text(encoding="utf-8")))}
    return {}


def persist_reviewer_signoff(run_dir: Path, reviewer: str) -> ReviewerSignoff:
    from core.sr.signoff import approve

    holder = SimpleNamespace(reviewer_signoff=None)
    signoff = approve(holder, reviewer)
    out = run_dir / SIGNOFF_ARTIFACT
    out.write_text(signoff.model_dump_json(indent=2), encoding="utf-8")
    return signoff


def load_reviewer_signoff(run_dir: Path) -> ReviewerSignoff | None:
    p = run_dir / SIGNOFF_ARTIFACT
    if not p.exists():
        return None
    return ReviewerSignoff.model_validate_json(p.read_text(encoding="utf-8"))


def apply_reviewer_signoff(run, run_dir: Path):
    signoff = load_reviewer_signoff(run_dir)
    if signoff is not None:
        run.reviewer_signoff = signoff
    return run


def _apply_harness_stage_log(run_dir: Path) -> None:
    from core.llm import client
    from core.tools import harness

    persisted = harness.read_stage_entries(run_dir)
    if not persisted:
        return
    merged = harness.merge_stage_entries(client.stage_log(), persisted)
    harness.replay_stage_entries(merged)


def load_run_bundle(run_dir: Path, vertical: str | None = None):
    if vertical is None:
        from core.sr.config import persisted_vertical
        vertical = persisted_vertical(run_dir)   # honor the run's own vertical, not env/default
    cfg = load_sr_config(vertical)
    _apply_harness_stage_log(run_dir)
    corpus = load_corpus(run_dir / "corpus.json", cfg)
    verified = {c.id for c in corpus.verified}
    screening = [s for s in load_tool_screening(run_dir) if s.pmid in verified]
    extractions = load_tool_extractions(run_dir, cfg)
    from core.sr.prescreen import load_prescreen_report
    run = apply_reviewer_signoff(
        build_run(corpus, screening, extractions, prescreen=load_prescreen_report(run_dir)),
        run_dir,
    )
    return SimpleNamespace(cfg=cfg, corpus=corpus, run=run, records=load_records(run_dir))


def load_rob(run_dir: Path) -> list[RoBAssessment]:
    from core.sr.rob import normalize

    indexed = sorted(run_dir.glob("rob_idx_*.json"))
    if indexed:
        out = []
        for p in indexed:
            out.append(normalize(RoBAssessment.model_validate_json(p.read_text(encoding="utf-8"))))
        return out
    p = run_dir / "rob.json"
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("assessments", [])
    return [normalize(RoBAssessment.model_validate(x)) for x in items]


def load_grade(run_dir: Path) -> list[GradeRow]:
    p = run_dir / "grade.json"
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("rows", [])
    return [GradeRow.model_validate(x) for x in items]


def _objectify(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _objectify(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_objectify(v) for v in value]
    return value


def load_stats(run_dir: Path):
    """Load recipe-produced stats first, then fall back to single-tool artifacts."""
    configured = run_dir / "stats_configured.json"
    if configured.exists():
        return _objectify(json.loads(configured.read_text(encoding="utf-8")))

    prop = run_dir / "stats_proportions.json"
    if prop.exists():
        raw = json.loads(prop.read_text(encoding="utf-8"))
        outcome = SimpleNamespace(
            field=raw.get("field", ""),
            label=raw.get("label") or raw.get("field", ""),
            n_pooled=raw.get("n_pooled", 0),
            n_excluded=raw.get("n_excluded", 0),
            forest_png="",
            results=[
                SimpleNamespace(
                    group=r.get("group", ""),
                    k=r.get("k", 0),
                    prop=(r.get("prop") or 0),
                    ci_lb=(r.get("ci") or [0, 0])[0],
                    ci_ub=(r.get("ci") or [0, 0])[1],
                    I2=(r.get("I2") or 0),
                )
                for r in raw.get("results", [])
            ],
        )
        return SimpleNamespace(
            available=bool(raw.get("available")),
            note=raw.get("note", ""),
            outcomes=[outcome],
            hr_outcomes=[],
            bin_outcomes=[],
            cont_outcomes=[],
            meta_regression=None,
        )
    return SimpleNamespace(
        available=False, note="no stats artifact found", outcomes=[], hr_outcomes=[],
        bin_outcomes=[], cont_outcomes=[], meta_regression=None)
