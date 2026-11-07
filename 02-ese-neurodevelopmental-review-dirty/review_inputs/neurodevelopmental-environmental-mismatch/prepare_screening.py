"""Deterministic evidence-hierarchy triage for the bounded NDEM corpora.

This is not eligibility screening. It selects a transparent, fixed-size subset
for AI Reviewer-1 screening and labels all other records as not advanced because
of the registered rapid-review cap.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MODULE_KEYWORDS = {
    "ndem-a-persistence-compensation": [
        "persistence", "longitudinal", "follow-up", "adult", "executive",
        "impairment", "intelligence", "gifted", "achievement", "compensation",
        "masking",
    ],
    "ndem-b-overlap-social-dcd": [
        "overlap", "comorbid", "differential", "social communication", "peer",
        "bullying", "motor", "coordination", "dysprax", "adaptive",
    ],
    "ndem-c-context-motivation-strengths": [
        "interest", "novelty", "reward", "urgency", "feedback", "environment",
        "workplace", "autonomy", "hyperfocus", "creativ", "divergent",
        "pattern recognition",
    ],
    "ndem-d-external-scaffolding": [
        "coach", "cognitive behavioral", "organizational", "checklist", "reminder",
        "digital", "assistive", "scaffold", "accommodation", "artificial intelligence",
        "randomized",
    ],
    "ndem-e1-sleep-circadian": [
        "sleep", "insomnia", "circadian", "shift work", "attention", "executive",
        "cognition", "mania", "bipolar", "irritab",
    ],
    "ndem-e2-mood-substances": [
        "cannabis", "thc", "marijuana", "stimulant", "methylphenidate",
        "amphetamine", "nicotine", "mania", "hypomania", "psychosis", "activation",
        "mood", "cognition",
    ],
    "ndem-f-daydreaming-rejection": [
        "maladaptive daydream", "immersive daydream", "loneliness", "rejection",
        "bullying", "shame", "boredom", "anxiety", "trauma", "dissociation",
        "emotion regulation",
    ],
}

SYNTHESIS_MARKERS = (
    "systematic review",
    "meta-analysis",
    "meta analysis",
    "practice guideline",
    "clinical guideline",
    "consensus statement",
    "umbrella review",
    "scoping review",
)

PRIMARY_MARKERS = (
    "randomized",
    "randomised",
    "clinical trial",
    "cohort",
    "longitudinal",
    "prospective",
    "retrospective",
    "case-control",
    "case control",
    "comparative study",
    "multicenter",
    "within-person",
    "follow-up",
)

LOW_VALUE_TYPES = (
    "editorial",
    "letter",
    "comment",
    "erratum",
    "news",
    "protocol",
    "case report",
)


def _tier(record: dict) -> tuple[int, str]:
    title_and_type = " ".join(
        [record.get("title") or "", record.get("study_type") or ""]
    ).lower()
    text = f"{title_and_type} {record.get('abstract') or ''}".lower()
    study_type = (record.get("study_type") or "").lower()
    # An abstract may cite or discuss a prior review; that does not make the
    # current record a synthesis. Restrict synthesis classification to title/type.
    if any(marker in title_and_type for marker in SYNTHESIS_MARKERS):
        return 1, "evidence synthesis or guideline"
    if any(marker in text for marker in PRIMARY_MARKERS):
        return 2, "higher-priority primary design"
    if any(marker in study_type for marker in LOW_VALUE_TYPES):
        return 4, "low-value publication type for this rapid review"
    return 3, "other potentially empirical record"


def _score(record: dict, module: str, tier: int) -> int:
    text = f"{record.get('title', '')} {record.get('abstract', '')}".lower()
    score = {1: 1000, 2: 600, 3: 200, 4: 0}[tier]
    score += 15 * sum(1 for keyword in MODULE_KEYWORDS[module] if keyword in text)
    year = record.get("year")
    if isinstance(year, int):
        score += max(0, min(26, year - 2000))
    if record.get("abstract") in ("", "[no abstract available]"):
        score -= 80
    return score


def prepare_module(run_dir: Path, *, cap: int) -> dict:
    module = run_dir.name
    records = json.loads((run_dir / "records_for_agents.json").read_text())
    ranked = []
    for record in records:
        tier, tier_reason = _tier(record)
        ranked.append(
            {
                **record,
                "module": module,
                "triage_tier": tier,
                "triage_tier_reason": tier_reason,
                "triage_score": _score(record, module, tier),
            }
        )
    ranked.sort(
        key=lambda row: (
            row["triage_tier"],
            -row["triage_score"],
            -(row.get("year") or 0),
            row["pmid"],
        )
    )
    candidates = ranked[:cap]
    deferred = [
        {
            "pmid": row["pmid"],
            "module": module,
            "title": row["title"],
            "year": row.get("year"),
            "study_type": row.get("study_type", ""),
            "status": "not_advanced_due_rapid_review_cap",
            "reason": (
                f"Ranked below the prespecified top {cap} records for this module; "
                "not an eligibility exclusion."
            ),
            "triage_tier": row["triage_tier"],
            "triage_score": row["triage_score"],
        }
        for row in ranked[cap:]
    ]
    (run_dir / "screening_candidates.json").write_text(
        json.dumps(candidates, indent=2), encoding="utf-8"
    )
    (run_dir / "screening_deferred.json").write_text(
        json.dumps(deferred, indent=2), encoding="utf-8"
    )
    scope = {
        "module": module,
        "verified_records": len(records),
        "candidate_cap": cap,
        "advanced_to_ai_reviewer_1": len(candidates),
        "not_advanced_due_rapid_review_cap": len(deferred),
        "candidate_tier_counts": {
            str(tier): sum(1 for row in candidates if row["triage_tier"] == tier)
            for tier in (1, 2, 3, 4)
        },
        "deferred_is_not_exclusion": True,
    }
    (run_dir / "screening_scope.json").write_text(
        json.dumps(scope, indent=2), encoding="utf-8"
    )
    return scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("runs/ndem-rapid-20260729"),
    )
    parser.add_argument("--cap", type=int, default=140)
    args = parser.parse_args()
    scopes = [
        prepare_module(run_dir, cap=args.cap)
        for run_dir in sorted(args.run_root.glob("ndem-*"))
        if (run_dir / "records_for_agents.json").exists()
    ]
    (args.run_root / "screening_scope_summary.json").write_text(
        json.dumps(scopes, indent=2), encoding="utf-8"
    )
    print(json.dumps(scopes, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
