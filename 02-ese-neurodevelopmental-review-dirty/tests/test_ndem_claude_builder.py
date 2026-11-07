"""Offline regression tests for the blinded Claude review builder."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


BUILDER_DIR = (
    Path(__file__).resolve().parents[1]
    / "review_inputs"
    / "neurodevelopmental-environmental-mismatch"
)
sys.path.insert(0, str(BUILDER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "ndem_claude_builder",
    BUILDER_DIR / "build_claude_secondary_review.py",
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def _extraction(pmid: str, marker: str) -> dict[str, object]:
    return {
        "pmid": pmid,
        "claim_ids": [f"unused-{marker}"],
        "direction": "neutral",
        "evidence_role": "neutral",
        "study_design": marker,
        "population": f"population-{marker}",
        "exposure_intervention": "NR",
        "comparator": "NR",
        "outcome": "outcome",
        "main_result": "result",
        "effect_estimate": "NR",
        "directness": "indirect",
        "major_limitations": "abstract only",
        "individual_applicability": "low",
        "provisional_quality": "low",
        "quality_reason": "abstract only",
        "evidence_basis": "abstract",
        "abstract_available": True,
        "fulltext_required": True,
    }


def _assessment(
    claim_id: str,
    *,
    supporting: list[str] | None = None,
    opposing: list[str] | None = None,
    qualifying: list[str] | None = None,
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "supporting_pmids": supporting or [],
        "opposing_pmids": opposing or [],
        "qualifying_pmids": qualifying or [],
    }


def _metadata(pmid: str) -> dict[str, object]:
    return {
        "pmid": pmid,
        "authors": ["Reviewer Test"],
        "title": "Frozen test record",
        "journal": "Test Journal",
        "year": "2026",
        "volume": "",
        "issue": "",
        "pages_or_article_number": "",
        "doi": "",
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def test_matrix_uses_assessment_links_and_preserves_source_module(
    tmp_path: Path,
) -> None:
    module_a = "ndem-a-persistence-compensation"
    module_b = "ndem-b-overlap-social-dcd"
    module_outputs = {
        module_a: {
            "extractions": [_extraction("123", "module-a")],
            "claim_assessments": [
                _assessment("H1.1", supporting=["123"])
            ],
        },
        module_b: {
            "extractions": [_extraction("123", "module-b")],
            "claim_assessments": [
                _assessment("H5.1", qualifying=["123"])
            ],
        },
    }
    global_output = {
        "claim_assessments": [_assessment("G1", opposing=["123"])]
    }

    path = builder.write_matrix(
        tmp_path, module_outputs, global_output, [_metadata("123")]
    )
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 4
    module_a_row = next(row for row in rows if row["claim_id"] == "H1.1")
    assert module_a_row["direction"] == "supports"
    assert module_a_row["evidence_role"] == "supporting"
    assert module_a_row["source_module"] == module_a
    assert module_a_row["study_design"] == "module-a"

    module_b_row = next(row for row in rows if row["claim_id"] == "H5.1")
    assert module_b_row["direction"] == "mixed"
    assert module_b_row["evidence_role"] == "qualifying"
    assert module_b_row["source_module"] == module_b
    assert module_b_row["study_design"] == "module-b"

    global_rows = [row for row in rows if row["claim_id"] == "G1"]
    assert {row["source_module"] for row in global_rows} == {
        module_a,
        module_b,
    }
    assert {row["study_design"] for row in global_rows} == {
        "module-a",
        "module-b",
    }
    assert {row["direction"] for row in global_rows} == {"opposes"}


def test_assessment_relationships_preserve_multiple_roles() -> None:
    assessment = _assessment(
        "H1.1", supporting=["123"], opposing=["123"], qualifying=["456"]
    )
    assert builder.assessment_relationships(assessment) == [
        ("123", "supports", "supporting"),
        ("123", "opposes", "opposing"),
        ("456", "mixed", "qualifying"),
    ]


def test_prisma_counts_use_grounding_and_scope_sources(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    modules = [module for module, _, _ in builder.MODULES]
    grounding = [
        {
            "vertical": module,
            "identified": index + 10,
            "deduped": index + 8,
            "verified": index + 7,
            "rejected": 1,
        }
        for index, module in enumerate(modules)
    ]
    scope = [
        {
            "module": module,
            "not_advanced_due_rapid_review_cap": index,
        }
        for index, module in enumerate(modules)
    ]
    _write_json(inputs / "grounding_summary.json", grounding)
    _write_json(inputs / "screening_scope_summary.json", scope)

    counts = builder.prisma_flow_counts(tmp_path)

    assert counts == {
        "identified": sum(row["identified"] for row in grounding),
        "deduped": sum(row["deduped"] for row in grounding),
        "verified": sum(row["verified"] for row in grounding),
        "rejected": 7,
        "deferred": sum(range(7)),
    }


def test_scientific_seal_verifies_chain_and_detects_mutation(
    tmp_path: Path,
) -> None:
    blind_root = tmp_path.resolve()
    required_inputs = [
        _write_json(blind_root / "inputs" / "claim_ledger.csv", []),
        _write_json(blind_root / "inputs" / "grounding_summary.json", []),
        _write_json(
            blind_root / "inputs" / "screening_scope_summary.json", []
        ),
    ]
    sealed_outputs: list[Path] = []
    for module, _, _ in builder.MODULES:
        module_input = blind_root / "inputs" / "modules" / module
        for name in (
            "screening_candidates.json",
            "search_execution.json",
            "search_manifest.json",
            "corpus.json",
        ):
            required_inputs.append(_write_json(module_input / name, {}))
        module_output = blind_root / "outputs" / "modules" / module
        sealed_outputs.extend(
            [
                _write_json(
                    module_output / "screening_ai_r2_claude.json", []
                ),
                _write_json(
                    module_output / "synthesis_ai_r2_claude.json", {}
                ),
            ]
        )
    sealed_outputs.append(
        _write_json(
            blind_root / "outputs" / "global_synthesis_ai_r2_claude.json",
            {},
        )
    )

    manifest_path = _write_json(
        blind_root / "provenance" / "blind_input_manifest.json",
        {
            "files": [
                {
                    "packet_path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": builder.sha256(path),
                }
                for path in required_inputs
            ]
        },
    )
    seal_path = _write_json(
        blind_root / "provenance" / "claude_scientific_output_seal.json",
        {
            "blind_input_manifest_sha256": builder.sha256(manifest_path),
            "gate_status": {"secondary_ai_reviewer_claude": "complete"},
            "files": [builder.hash_record(path) for path in sealed_outputs],
        },
    )

    verified_path, _, counts = builder.verify_scientific_seal(blind_root)

    assert verified_path == seal_path
    assert counts == {
        "manifest_files_verified": len(required_inputs),
        "sealed_files_verified": len(sealed_outputs),
    }

    sealed_outputs[0].write_text("[\"mutated\"]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        builder.verify_scientific_seal(blind_root)
