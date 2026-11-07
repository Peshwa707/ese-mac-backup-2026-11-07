#!/usr/bin/env python3
"""Validate abstract-basis AI Reviewer-1 extraction and claim coverage."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs" / "ndem-rapid-20260729"
MODULE_MAP = {
    "ndem-a-persistence-compensation": "A",
    "ndem-b-overlap-social-dcd": "B",
    "ndem-c-context-motivation-strengths": "C",
    "ndem-d-external-scaffolding": "D",
    "ndem-e1-sleep-circadian": "E1",
    "ndem-e2-mood-substances": "E2",
    "ndem-f-daydreaming-rejection": "F",
}
REQUIRED = {
    "pmid",
    "claim_ids",
    "direction",
    "evidence_role",
    "study_design",
    "population",
    "exposure_intervention",
    "comparator",
    "outcome",
    "main_result",
    "effect_estimate",
    "directness",
    "major_limitations",
    "individual_applicability",
    "provisional_quality",
    "quality_reason",
    "evidence_basis",
    "abstract_available",
    "fulltext_required",
}
OPTIONAL = {"review_status"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def nonempty_text(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value)
    return False


def main() -> None:
    ledger_path = (
        ROOT / "review_inputs" / "neurodevelopmental-environmental-mismatch" / "claim_ledger.csv"
    )
    with ledger_path.open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    claims_by_module: dict[str, set[str]] = {}
    for row in ledger:
        claims_by_module.setdefault(row["module"], set()).add(row["claim_id"])
    all_claims = {row["claim_id"] for row in ledger}

    supplement = read_json(
        ROOT
        / "review_inputs"
        / "neurodevelopmental-environmental-mismatch"
        / "targeted_pubmed_supplements.json"
    )
    supplemental_by_module = {
        module: {
            str(pmid)
            for search in supplement["searches"]
            if search["module"] == module
            for pmid in search["selected_pmids"]
        }
        for module in [*MODULE_MAP, "global"]
    }

    summary: dict[str, object] = {
        "status": "AI Reviewer-1 extraction validation; human Reviewer 2 pending",
        "modules": {},
    }
    selected_all: list[str] = []

    for module, ledger_module in MODULE_MAP.items():
        module_dir = RUN / module
        rows = read_json(module_dir / "evidence_extraction_ai_r1.json")
        screened = read_json(module_dir / "screening_ai_r1.json")
        screened_pmids = {
            str(row["pmid"])
            for row in screened
            if row["decision"] in {"include", "uncertain"}
        }
        candidates = {
            str(row["pmid"]): row
            for row in read_json(module_dir / "screening_candidates.json")
        }
        allowed = screened_pmids | supplemental_by_module.get(module, set())
        covered: set[str] = set()
        for row in rows:
            assert REQUIRED <= set(row) <= REQUIRED | OPTIONAL, (
                module,
                row.get("pmid"),
                set(row) ^ REQUIRED,
            )
            pmid = str(row["pmid"])
            assert pmid in allowed, (module, pmid, "not screened or supplemental")
            assert row["direction"] in {"supports", "opposes", "mixed", "neutral"}
            assert row["directness"] in {"direct", "indirect", "highly indirect"}
            assert row["provisional_quality"] in {"high", "moderate", "low", "very low"}
            assert row["evidence_basis"] == "abstract"
            assert row["abstract_available"] is True
            assert row["fulltext_required"] is True
            assert row["claim_ids"] and set(row["claim_ids"]) <= all_claims
            assert all(
                nonempty_text(row[field])
                for field in REQUIRED
                if field
                not in {
                    "pmid",
                    "claim_ids",
                    "abstract_available",
                    "fulltext_required",
                }
            )
            if pmid in candidates:
                assert str(candidates[pmid].get("abstract") or "").strip()
            covered.update(row["claim_ids"])
            selected_all.append(pmid)

        expected = claims_by_module[ledger_module]
        missing = sorted(expected - covered)
        synthesis_path = module_dir / "module_synthesis_ai_r1.md"
        synthesis = synthesis_path.read_text(encoding="utf-8")
        for claim_id in expected:
            assert claim_id in synthesis, f"{module}: {claim_id} missing from synthesis"
        assert "Human Reviewer 2" in synthesis

        extraction_path = module_dir / "evidence_extraction_ai_r1.json"
        summary["modules"][module] = {
            "selected_records": len(rows),
            "claims_covered": len(expected),
            "claims_with_selected_relationships": len(expected) - len(missing),
            "claims_without_selected_relationships": missing,
            "sha256": hashlib.sha256(extraction_path.read_bytes()).hexdigest(),
        }

    global_rows = read_json(RUN / "global_evidence_extraction_ai_r1.json")
    global_covered = {claim for row in global_rows for claim in row["claim_ids"]}
    assert claims_by_module["G"] <= global_covered | {"G1", "G2"}
    selected_all.extend(str(row["pmid"]) for row in global_rows)

    summary["totals"] = {
        "selected_occurrences": len(selected_all),
        "unique_pmids": len(set(selected_all)),
        "duplicate_occurrences": len(selected_all) - len(set(selected_all)),
        "modules": len(MODULE_MAP),
    }
    (RUN / "extraction_validation.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary["totals"], sort_keys=True))


if __name__ == "__main__":
    main()
