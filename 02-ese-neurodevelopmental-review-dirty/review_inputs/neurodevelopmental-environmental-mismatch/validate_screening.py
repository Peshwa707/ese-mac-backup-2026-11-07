#!/usr/bin/env python3
"""Validate all bounded AI Reviewer-1 screening outputs.

This validator deliberately keeps rapid-cap deferrals separate from screening
exclusions. It does not represent a second human review.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs" / "ndem-rapid-20260729"
MODULES = [
    "ndem-a-persistence-compensation",
    "ndem-b-overlap-social-dcd",
    "ndem-c-context-motivation-strengths",
    "ndem-d-external-scaffolding",
    "ndem-e1-sleep-circadian",
    "ndem-e2-mood-substances",
    "ndem-f-daydreaming-rejection",
]
REQUIRED = {
    "pmid",
    "module",
    "decision",
    "reason",
    "claim_ids",
    "evidence_role",
    "priority",
    "abstract_available",
    "fulltext_required",
}
DECISIONS = {"include", "exclude", "uncertain"}
PRIORITIES = {"high", "moderate", "low"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ledger_path = ROOT / "review_inputs" / "neurodevelopmental-environmental-mismatch" / "claim_ledger.csv"
    with ledger_path.open(newline="", encoding="utf-8") as handle:
        claim_ids = {row["claim_id"] for row in csv.DictReader(handle)}

    summary: dict[str, object] = {
        "status": "AI Reviewer-1 screening validation; human Reviewer 2 pending",
        "modules": {},
    }
    total = Counter()
    included_pmids: list[str] = []

    for module in MODULES:
        module_dir = RUN / module
        candidates = load_json(module_dir / "screening_candidates.json")
        screened_path = module_dir / "screening_ai_r1.json"
        screened = load_json(screened_path)

        assert len(candidates) == 140, (module, len(candidates))
        assert len(screened) == len(candidates), (module, len(screened))
        candidate_pmids = [str(row["pmid"]) for row in candidates]
        screened_pmids = [str(row["pmid"]) for row in screened]
        assert screened_pmids == candidate_pmids, f"{module}: ordered PMID mismatch"
        assert Counter(screened_pmids) == Counter(candidate_pmids), f"{module}: PMID multiplicity mismatch"

        for row in screened:
            assert set(row) == REQUIRED, (module, row.get("pmid"), set(row) ^ REQUIRED)
            assert row["module"] == module
            assert row["decision"] in DECISIONS
            assert row["priority"] in PRIORITIES
            assert isinstance(row["reason"], str) and row["reason"].strip()
            assert isinstance(row["claim_ids"], list)
            assert set(row["claim_ids"]) <= claim_ids
            assert isinstance(row["abstract_available"], bool)
            assert isinstance(row["fulltext_required"], bool)
            if row["decision"] in {"include", "uncertain"}:
                assert row["abstract_available"], (module, row["pmid"], "no inspected abstract")

        counts = Counter(row["decision"] for row in screened)
        total.update(counts)
        included_pmids.extend(
            str(row["pmid"]) for row in screened if row["decision"] in {"include", "uncertain"}
        )
        summary["modules"][module] = {
            "screened": len(screened),
            "include": counts["include"],
            "uncertain": counts["uncertain"],
            "exclude": counts["exclude"],
            "sha256": sha256(screened_path),
        }

    scope = load_json(RUN / "screening_scope_summary.json")
    advanced = sum(int(item["advanced_to_ai_reviewer_1"]) for item in scope)
    deferred = sum(int(item["not_advanced_due_rapid_review_cap"]) for item in scope)
    assert advanced == sum(total.values()) == 980

    summary["totals"] = {
        "screened": sum(total.values()),
        "include": total["include"],
        "uncertain": total["uncertain"],
        "exclude": total["exclude"],
        "deferred_not_screened": deferred,
        "unique_include_or_uncertain_pmids": len(set(included_pmids)),
        "include_or_uncertain_duplicate_occurrences": len(included_pmids) - len(set(included_pmids)),
    }

    output = RUN / "screening_validation.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary["totals"], sort_keys=True))


if __name__ == "__main__":
    main()
