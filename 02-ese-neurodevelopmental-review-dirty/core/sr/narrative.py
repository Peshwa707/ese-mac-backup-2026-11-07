"""Evidence brief + citation validation for AI-drafted narrative prose (Step 10).

The brief is a deterministic, grounded summary (pooled estimates, GRADE, RoB,
counts) — the ONLY material a drafting agent may write from. After drafting,
citation validation confirms every [PMID: …] the prose cites is a verified
included study (or a named prior-review seed); anything else is flagged, not
shipped silently. Prose remains an AI draft requiring human approval.
"""

from __future__ import annotations

import re
from collections import Counter

from core.sr.config import SRConfig
from core.sr.models import GradeRow, RoBAssessment, ScreenDecision, SRRun

_PMID_RE = re.compile(r"PMID:\s*(\d+)", re.I)


def valid_citation_pmids(run: SRRun, seed_pmids: list[str]) -> set[str]:
    included = {s.pmid for s in run.screening if s.decision == ScreenDecision.INCLUDE}
    return included | set(seed_pmids)


def validate_citations(prose: str, valid: set[str]) -> tuple[list[str], list[str]]:
    """Return (cited_valid, cited_invalid) PMIDs found in the prose."""
    cited = set(_PMID_RE.findall(prose or ""))
    return sorted(cited & valid), sorted(cited - valid)


def build_evidence_brief(
    run: SRRun, cfg: SRConfig, stats, grade: list[GradeRow],
    rob: list[RoBAssessment], seed_pmids: list[str],
    included: list[dict] | None = None,
) -> str:
    from core.sr.stats.prepare import canonical_subgroup

    c = run.counts
    rob_overall = Counter(a.overall for a in (rob or []))
    lines = [
        "# GROUNDED EVIDENCE BRIEF (write ONLY from this; cite [PMID: n] from included studies)",
        "",
    ]
    # Per-arm study counts only when the vertical defines synthesis arms.
    arms = cfg.synthesis_arms
    if arms:
        sub = Counter(canonical_subgroup(
                          str(getattr(s, cfg.subgroup_field, "") or s.modality or ""),
                          cfg.subgroup_map)
                      for s in run.screening if s.decision == ScreenDecision.INCLUDE)
        breakdown = ", ".join(f"{a} {sub.get(a, 0)}" for a in arms)
        lines.append(f"- Included studies: {c.included} ({breakdown}); "
                     f"{c.uncertain} uncertain pending full text.")
    else:
        lines.append(f"- Included studies: {c.included}; {c.uncertain} uncertain pending full text.")
    lines += [
        f"- Screened {c.screened} verified records of {c.identified_pubmed} identified.",
        f"- Risk of bias (overall): {dict(rob_overall)}.",
    ]
    if cfg.indirect_comparison:
        between = " vs ".join(arms) if arms else "the comparison groups"
        lines.append(f"- No head-to-head RCT between {between} exists (indirect comparison).")
    lines += ["", "## Pooled estimates (random-effects)"]
    if stats and getattr(stats, "available", False):
        for oc in stats.outcomes:
            for r in oc.results:
                lines.append(f"- {oc.label} [{r.group}]: {r.prop:.1f}% "
                             f"(95% CI {r.ci_lb:.1f}–{r.ci_ub:.1f}; I²={r.I2:.0f}%; k={r.k})")
        for oc in getattr(stats, "hr_outcomes", []):
            for r in oc.results:
                lines.append(f"- {oc.label} [{r.group}]: {r.hr:.2f} "
                             f"(95% CI {r.ci_lb:.2f}–{r.ci_ub:.2f}; I²={r.I2:.0f}%; k={r.k})")
    else:
        lines.append("- (not computed)")
    lines += ["", "## GRADE certainty (per outcome)"]
    for g in grade or []:
        scope = f" [{g.stratum}]" if getattr(g, "stratum", "overall") != "overall" else ""
        lines.append(f"- {g.outcome}{scope}: {g.certainty} (start {g.start}; "
                     f"{len(g.downgrades)} downgrade(s); n={g.n_studies})")
    if seed_pmids:
        lines += ["", "## Prior-review seeds to compare against",
                  "- " + ", ".join(f"[PMID: {p}]" for p in seed_pmids)]
    lines += [
        "", "## Named pivotal trials captured",
        "- " + ", ".join(t for t, hit in run.named_trial_capture.items() if hit),
    ]
    if included:
        lines += ["", "## Included studies (citable PMIDs — cite as [PMID: n])"]
        for s in included:
            lines.append(f"- [PMID: {s['pmid']}] ({s.get('modality','?')}) {s.get('title','')[:90]}")
    return "\n".join(lines)
