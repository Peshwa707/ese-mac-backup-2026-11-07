"""Export a manuscript package from an existing SR run directory.

    python -m core.sr.manuscript <run_dir>

Loads the verified corpus + screening + extractions a run produced, reassembles
the SRRun via build_run, and writes the per-component MD/DOCX/PDF project folder
under the engine output base (~/Documents/EvidenceEngine/<vertical>/ by default).
Reuses runs/<id>/honesty_audit.json if present; otherwise computes the offline
quote-anchoring audit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.sr.agent_io import load_corpus, load_extractions, load_screening
from core.sr.audit import audit_quote_anchoring
from core.sr.config import load_sr_config
from core.sr.extract import _fill_missing_nr
from core.sr.manuscript.export import export_project
from core.sr.models import ScreenDecision
from core.sr.pipeline import build_run


def _load_run(run_dir: Path, dedup_trials: bool = False):
    from core.sr.config import persisted_vertical
    cfg = load_sr_config(persisted_vertical(run_dir))   # honor the run's vertical, not env/default
    corpus = load_corpus(run_dir / "corpus.json", cfg)
    records = {r["pmid"]: r for r in json.loads((run_dir / "records_for_agents.json").read_text())}

    vids = {c.id for c in corpus.verified}
    screening = [s for s in load_screening(json.loads((run_dir / "screening_merged.json").read_text()))
                 if s.pmid in vids]

    includes = json.loads((run_dir / "includes.json").read_text())
    merged_path = run_dir / "extractions_merged.json"
    if merged_path.exists():
        # Prefer the full-text-overlay merged extractions when present (Phase 1).
        extractions = [_fill_missing_nr(es, cfg)
                       for es in load_extractions(json.loads(merged_path.read_text()))]
    else:
        extractions = []
        for i, pmid in enumerate(includes):
            p = run_dir / f"extract_idx_{i:03d}.json"
            if not p.exists():
                continue
            es = load_extractions([json.loads(p.read_text())])[0]
            es.pmid = pmid
            extractions.append(_fill_missing_nr(es, cfg))

    if dedup_trials:
        extractions = _dedup_to_trials(run_dir, includes, records, extractions,
                                       {s.pmid: s.modality for s in screening})

    from core.sr.prescreen import load_prescreen_report
    run = build_run(corpus, screening, extractions, prescreen=load_prescreen_report(run_dir))
    return cfg, run, records, corpus


def _dedup_to_trials(run_dir, includes, records, extractions, modality_by_pmid):
    """Collapse publication-level extractions into trial-level (NCT/acronym), write a report."""
    import asyncio
    from core.connectors.pubmed import PubMedConnector
    from core.sr.dedup_trials import dedup_summary, group_by_trial

    async def _ncts():
        conn = PubMedConnector()
        try:
            return await conn.fetch_ncts(includes)
        finally:
            await conn.aclose()

    nct = asyncio.run(_ncts())
    groups = group_by_trial(extractions, records, nct)
    for g in groups:                       # carry modality from the anchor report
        g.extraction.modality = g.extraction.modality or modality_by_pmid.get(g.anchor_pmid, "")
    summary = dedup_summary(groups, len(extractions))
    report = {"summary": summary, "trials": [
        {"trial_id": g.trial_id, "key_source": g.key_source, "anchor": g.anchor_pmid,
         "members": g.member_pmids, "conflicts": g.conflicts}
        for g in groups]}
    (run_dir / "dedup_report.json").write_text(json.dumps(report, indent=2))
    print(f"  trial dedup: {summary['reports']} reports -> {summary['trials']} trials "
          f"({summary['by_key_source']}); report -> dedup_report.json")
    return [g.extraction for g in groups]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sr-manuscript", description="Export an SR run to a manuscript project folder.")
    ap.add_argument("run_dir", type=Path, help="Path to runs/<id>/ produced by the SR pipeline.")
    ap.add_argument("--base-dir", type=Path, default=None,
                    help="Override the engine output base (~/Documents/EvidenceEngine/<vertical>/).")
    ap.add_argument("--day", default=None, help="Date stamp for the project folder (YYYY-MM-DD).")
    ap.add_argument("--profile", default=None,
                    help="Review type: systematic | scoping | rapid | living (default: systematic).")
    ap.add_argument("--dedup-trials", action="store_true",
                    help="Group multiple reports of one trial (by NCT/acronym) and pool per trial.")
    args = ap.parse_args(argv)

    run_dir = args.run_dir.expanduser().resolve()
    if not (run_dir / "corpus.json").exists():
        print(f"ERROR: {run_dir} is not an SR run dir (no corpus.json).", file=sys.stderr)
        sys.exit(1)

    # Export gate (orchestrator honesty gates §7a/§7b). Refuse BEFORE any loading or
    # network work when an orchestrated run has not cleared its gates; legacy/manual
    # runs (no run_state.json) return None and stay ungated for backward compatibility.
    from core.sr.orchestrator.engine import export_gate
    gate = export_gate(run_dir)
    if gate is not None and gate[0] is False:
        # Write the block notice + PURGE any prior rendered dossier (so a stale,
        # downloadable manuscript can't survive a later blocked export), without the
        # expensive load/network the full export does.
        from core.sr.config import load_sr_config
        from core.sr.manuscript.export import write_blocked_export
        from core.sr.profile import load_profile
        res = write_blocked_export(load_sr_config(), gate[1], base_dir=args.base_dir,
                                   day=args.day, review_type=load_profile(args.profile).name)
        print(f"ERROR: export blocked — {run_dir} has not cleared its honesty gates:",
              file=sys.stderr)
        for r in gate[1]:
            print(f"  - {r}", file=sys.stderr)
        print(f"Block notice written to {res['project_dir']}. Have a human reviewer "
              "sign the required approvals, then re-export.", file=sys.stderr)
        sys.exit(2)

    cfg, run, records, _ = _load_run(run_dir, dedup_trials=args.dedup_trials)

    audit_path = run_dir / "honesty_audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
    else:
        abstracts = {p: records[p].get("abstract", "") for p in records}
        audit = {"anchoring": audit_quote_anchoring(abstracts, run.extractions)}

    # Risk-of-bias assessments (Phase 2), if produced.
    rob = None
    rob_files = sorted(run_dir.glob("rob_idx_*.json"))
    if rob_files:
        from core.sr.models import RoBAssessment
        from core.sr.rob import normalize
        rob = []
        for p in rob_files:
            try:
                rob.append(normalize(RoBAssessment.model_validate_json(p.read_text())))
            except Exception:
                continue

    # GRADE certainty (Phase 3) — computed deterministically from extractions + RoB.
    from core.sr.grade import grade_outcomes
    screening = [s for s in load_screening(json.loads((run_dir / "screening_merged.json").read_text()))]
    grade = grade_outcomes(run.extractions, screening, rob)
    modality_by_pmid = {s.pmid: s.modality for s in screening}

    # PROSPERO registration draft + best-effort duplicate check (Phase 5).
    import asyncio
    from dataclasses import asdict
    from core.sr.prospero import duplicate_check, registration_fields
    dup = asyncio.run(duplicate_check(f"{cfg.review_title} systematic review"))
    prospero = {"fields": registration_fields(cfg, run), "dup": asdict(dup)}

    # AI-drafted, citation-checked narrative prose (Phase 6), if produced.
    narrative = None
    nar_path = run_dir / "narrative_final.json"
    if nar_path.exists():
        from core.sr.narrative import validate_citations, valid_citation_pmids
        narrative = json.loads(nar_path.read_text())
        # Valid citations are the run's own included studies — NOT hardcoded PMIDs (two
        # myeloma prior-review PMIDs used to be whitelisted here, so a non-myeloma review
        # could ship a citation to an unrelated study).
        valid = valid_citation_pmids(run, [])
        _, invalid = validate_citations(" ".join(narrative.values()), valid)
        if invalid:  # defense-in-depth: never ship prose citing unverified PMIDs
            print(f"  WARNING: narrative cited invalid PMIDs {invalid}; dropping prose.", file=sys.stderr)
            narrative = None

    # Reference-integrity gate (WP7): if a key-based verified reference library is
    # present (run_dir/references.json), the manuscript must not cite a broken or
    # unverified reference — fail closed before export. No-op when absent.
    references = None
    if narrative:
        from core.sr.references import gate_run_dir, load_library
        from core.sr.references_build import normalize_pmid_citations
        gate_run_dir(run_dir, normalize_pmid_citations(
            " ".join(str(v) for v in narrative.values())))
        ref_path = run_dir / "references.json"
        if ref_path.exists():
            references = load_library(ref_path)

    from core.sr.profile import load_profile
    profile = load_profile(args.profile)

    result = export_project(run, cfg, records, audit=audit, rob=rob, grade=grade,
                            modality_by_pmid=modality_by_pmid, prospero=prospero,
                            narrative=narrative, references=references, profile=profile,
                            base_dir=args.base_dir, day=args.day, gate=gate)
    print(f"Exported {result['n_components']} components -> {result['project_dir']}")
    for w in result["warnings"]:
        print(f"  WARNING: {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
