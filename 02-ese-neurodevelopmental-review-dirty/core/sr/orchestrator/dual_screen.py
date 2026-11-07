"""Dual independent-AI screening (WP-M2.3, spec §4/§7a/§8).

Runs Codex AI Reviewer 1 and a secondary Claude AI reviewer as INDEPENDENT models
(different families/endpoints — enforced by a preflight), reconciles their decisions,
and produces a consensus that is a DRAFT: agreements pass through, disagreements
become UNCERTAIN flagged for human Reviewer 2 adjudication (the honesty gate). The
agreement statistic is reported as ``llm_llm_agreement`` — explicitly NOT inter-rater
reliability (two LLMs share training and failure modes; a human-involving κ remains
the reliability anchor).
"""

from __future__ import annotations

from pathlib import Path

from core.sr.models import ScreenDecision, ScreeningResult
from core.sr.orchestrator.adapters import _hash_of, _load_citations, write_json
from core.sr.orchestrator.reviewers import assert_independent, resolve_reviewer
from core.sr.orchestrator.stages import RunContext, StageResult

_SCREEN_BATCH = 10


def _screen_with_spec(records: list, cfg, spec, *, stage: str) -> list[ScreeningResult]:
    from core.llm.gateway import gateway_parse
    from core.sr.models import ScreeningBatch
    from core.sr.screen import _guarded_results, build_screening_prompt

    out: list[ScreeningResult] = []
    for i in range(0, len(records), _SCREEN_BATCH):
        chunk = records[i:i + _SCREEN_BATCH]
        system, user = build_screening_prompt(chunk, cfg)
        # reviewer-specific stage tag so provenance/stage_log audits which resolved
        # model was R1 vs R2 (not a generic "screening" entry).
        batch = gateway_parse(ScreeningBatch, system, user, stage=stage,
                              spec=spec, max_tokens=8000)
        out.extend(_guarded_results(chunk, batch.results))
    return out


def _conf(r: ScreeningResult | None) -> float:
    return float(r.confidence) if r and r.confidence is not None else 0.5


def dual_screen_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    r1, r2 = resolve_reviewer(ctx.cfg, "r1"), resolve_reviewer(ctx.cfg, "r2")
    assert_independent(r1, r2)                       # fail closed before any model call

    to_screen = _load_citations(run_dir, "to_screen.json")
    res1 = _screen_with_spec(to_screen, ctx.cfg, r1, stage="screening_r1") if to_screen else []
    res2 = _screen_with_spec(to_screen, ctx.cfg, r2, stage="screening_r2") if to_screen else []
    write_json(run_dir, "screening_r1.json", res1)
    write_json(run_dir, "screening_r2.json", res2)

    from core.sr.reconcile import reconcile
    d1 = {x.pmid: x for x in res1}
    d2 = {x.pmid: x for x in res2}
    # Build the agreement maps over the FULL to_screen id space (a reviewer that drops a
    # record defaults to "uncertain") so llm_llm_agreement's denominator matches the
    # consensus/conflict denominator instead of only the records each reviewer returned.
    ids = [c.id for c in to_screen]
    rec = reconcile({p: (d1[p].decision.value if p in d1 else "uncertain") for p in ids},
                    {p: (d2[p].decision.value if p in d2 else "uncertain") for p in ids})

    consensus: list[ScreeningResult] = []
    conflicts: list[str] = []
    for c in to_screen:
        a, b = d1.get(c.id), d2.get(c.id)
        da = a.decision if a else ScreenDecision.UNCERTAIN
        db = b.decision if b else ScreenDecision.UNCERTAIN
        stream = (a.evidence_stream if a and a.evidence_stream else
                  (b.evidence_stream if b else ""))
        if da == db:
            consensus.append(ScreeningResult(pmid=c.id, decision=da, reason="R1=R2 agree",
                                             confidence=min(_conf(a), _conf(b)),
                                             evidence_stream=stream))
        else:
            conflicts.append(c.id)
            consensus.append(ScreeningResult(
                pmid=c.id, decision=ScreenDecision.UNCERTAIN,
                reason=f"reviewer conflict R1={da.value}/R2={db.value} — human adjudication required",
                confidence=0.0, evidence_stream=stream))
    cp, _ = write_json(run_dir, "screening.json", consensus)
    from core.sr.screen import subgroup_coverage_errors
    stream_errors = subgroup_coverage_errors(consensus, ctx.cfg)
    if stream_errors:
        raise RuntimeError("Screening stream validation failed: " + "; ".join(stream_errors))

    reviewers = {
        "ai_reviewer_1": f"{r1.source}/{r1.model}",
        "ai_reviewer_2": f"{r2.source}/{r2.model}",
        # These are intentionally pending here; this artifact cannot satisfy either
        # human governance gate.
        "human_reviewer_2": "pending",
        "clinical_signoff": "pending",
    }
    write_json(run_dir, "reconcile.json", {
        **rec, "reviewers": reviewers,
        "metric": "llm_llm_agreement",
        "metric_note": "Agreement between two LLM reviewers; NOT inter-rater reliability "
                       "(use a human-involving kappa as the reliability anchor).",
        "conflicts": conflicts,
    })

    summary = {
        "n": len(to_screen),
        "llm_llm_agreement": rec["kappa_nominal"],
        "llm_llm_agreement_weighted": rec["kappa_weighted"],
        "n_conflicts": len(conflicts),
        "n_hard_conflicts": rec["n_hard_conflicts"],
        "reviewers": reviewers,
        "note": "consensus is a DRAFT; conflicts are UNCERTAIN pending human Reviewer 2 adjudication",
    }
    return StageResult(
        outputs={"screening.json": cp,
                 "screening_r1.json": run_dir / "screening_r1.json",
                 "screening_r2.json": run_dir / "screening_r2.json",
                 "reconcile.json": run_dir / "reconcile.json"},
        summary=summary,
        deterministic_inputs={"to_screen.json": _hash_of(run_dir, "to_screen.json")},
    )
