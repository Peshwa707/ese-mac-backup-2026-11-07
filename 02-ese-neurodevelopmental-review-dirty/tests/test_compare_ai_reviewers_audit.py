from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "review_inputs"
    / "neurodevelopmental-environmental-mismatch"
    / "compare_ai_reviewers.py"
)
SPEC = importlib.util.spec_from_file_location("compare_ai_reviewers", SCRIPT)
assert SPEC and SPEC.loader
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scientific_seal_is_required(tmp_path):
    with pytest.raises(FileNotFoundError, match="scientific seal required"):
        comparison.verify_scientific_seal(tmp_path)


def test_main_checks_seal_before_creating_output(tmp_path, monkeypatch):
    output = tmp_path / "comparison"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--blind-root",
            str(tmp_path / "blind"),
            "--r2-output",
            str(tmp_path / "r2"),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(FileNotFoundError, match="scientific seal required"):
        comparison.main()

    assert not output.exists()


def test_scientific_seal_tamper_fails_closed(tmp_path, monkeypatch):
    module = "ndem-f-daydreaming-rejection"
    monkeypatch.setattr(comparison, "MODULES", [(module, "F")])
    manifest = tmp_path / "provenance" / "blind_input_manifest.json"
    screening = (
        tmp_path
        / "outputs"
        / "modules"
        / module
        / "screening_ai_r2_claude.json"
    )
    synthesis = screening.with_name("synthesis_ai_r2_claude.json")
    global_output = tmp_path / "outputs" / "global_synthesis_ai_r2_claude.json"
    for path, value in (
        (manifest, {"frozen": True}),
        (screening, [{"pmid": "1"}]),
        (synthesis, {"extractions": []}),
        (global_output, {"global_null_hypothesis": "indeterminate"}),
    ):
        _write_json(path, value)
    sealed_files = [screening, synthesis, global_output]
    seal = {
        "status": (
            "Claude blinded scientific judgments validated and sealed before "
            "Codex comparison"
        ),
        "blind_input_manifest_sha256": _sha256(manifest),
        "gate_status": {
            "secondary_ai_reviewer_claude": "complete",
            "ai_ai_reconciliation": "not started",
        },
        "files": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sealed_files
        ],
    }
    _write_json(
        tmp_path / "provenance" / "claude_scientific_output_seal.json",
        seal,
    )

    verified = comparison.verify_scientific_seal(tmp_path)
    assert verified["verified_files"] == 3

    screening.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch against scientific seal"):
        comparison.verify_scientific_seal(tmp_path)


def test_screening_keeps_occurrences_and_reports_nonempty_claim_jaccard(
    tmp_path, monkeypatch
):
    module = "ndem-f-daydreaming-rejection"
    monkeypatch.setattr(comparison, "MODULES", [(module, "F")])
    monkeypatch.setattr(comparison, "R1_RUN", tmp_path / "r1")
    pmids = [str(value) for value in range(1, 140)] + ["139"]
    r1 = []
    r2 = []
    for position, pmid in enumerate(pmids, start=1):
        decision = "include" if position % 2 else "exclude"
        left_claims = ["H1.1"] if position in {1, 2} else []
        right_claims = (
            ["H1.1"]
            if position == 1
            else ["H1.2"]
            if position == 2
            else []
        )
        common = {
            "pmid": pmid,
            "decision": decision,
            "priority": "moderate",
            "evidence_role": "neutral",
            "reason": "fixed synthetic decision",
        }
        r1.append({**common, "claim_ids": left_claims})
        r2.append({**common, "claim_ids": right_claims})
    _write_json(tmp_path / "r1" / module / "screening_ai_r1.json", r1)
    _write_json(
        tmp_path
        / "outputs"
        / "modules"
        / module
        / "screening_ai_r2_claude.json",
        r2,
    )

    rows, metrics = comparison.screening_comparison(tmp_path)
    pooled = metrics["pooled"]

    assert len(rows) == 140
    assert pooled["primary_pairing_key"] == ["module", "occurrence_position"]
    assert pooled["claim_id_annotation_nonempty_union_pairs"] == 2
    assert pooled["mean_claim_id_jaccard_nonempty_union"] == pytest.approx(0.5)
    assert pooled["claim_id_annotation_both_empty_pairs"] == 138
    assert pooled["claim_id_annotation_both_empty_prevalence"] == pytest.approx(
        138 / 140
    )
    assert rows[2]["claim_id_jaccard"] is None
    sensitivity = pooled["within_module_unique_pmid_sensitivity_analysis"]
    assert sensitivity["n"] == 139
    assert sensitivity["excluded_duplicate_occurrences"] == 1
    assert sensitivity["duplicate_occurrences"] == [
        {
            "module": module,
            "module_code": "F",
            "pmid": "139",
            "occurrence_positions": [139, 140],
            "extra_occurrences": 1,
        }
    ]


def test_extraction_selection_excludes_r1_records_outside_frozen_candidates(
    tmp_path, monkeypatch
):
    module = "ndem-e2-mood-substances"
    monkeypatch.setattr(comparison, "MODULES", [(module, "E2")])
    monkeypatch.setattr(comparison, "R1_RUN", tmp_path / "r1")
    _write_json(
        tmp_path / "inputs" / "modules" / module / "screening_candidates.json",
        [{"pmid": "1"}],
    )
    _write_json(
        tmp_path / "r1" / module / "evidence_extraction_ai_r1.json",
        [{"pmid": "1"}, {"pmid": "2"}],
    )
    _write_json(
        tmp_path
        / "outputs"
        / "modules"
        / module
        / "synthesis_ai_r2_claude.json",
        {"extractions": [{"pmid": "1"}]},
    )

    metrics = comparison.extraction_selection(tmp_path)

    assert metrics["reviewer_1_module_pmid_pairs"] == 1
    assert metrics["reviewer_1_outside_frozen_candidate_pairs_excluded"] == 1
    assert metrics["per_module"][module][
        "reviewer_1_outside_frozen_candidate_pmids_excluded"
    ] == ["2"]
    assert metrics["shared_module_pmid_pairs"] == 1


def test_evidence_sets_preserve_module_pmid_occurrences(tmp_path):
    matrix = tmp_path / "claim_evidence_matrix.csv"
    with matrix.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "claim_id",
                "module",
                "source_module",
                "pmid",
                "direction",
            ],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "claim_id": "H1.1",
                    "module": "module-a",
                    "source_module": "",
                    "pmid": "1",
                    "direction": "supports",
                },
                {
                    "claim_id": "H1.1",
                    "module": "global",
                    "source_module": "module-b",
                    "pmid": "1",
                    "direction": "opposes",
                },
                {
                    "claim_id": "H1.1",
                    "module": "module-a",
                    "source_module": "",
                    "pmid": "2",
                    "direction": "supports",
                },
            ]
        )
    frozen = {"module-a": {"1"}, "module-b": {"1"}}

    by_claim, directions, excluded = comparison.evidence_sets(matrix, frozen)

    assert by_claim["H1.1"] == {("module-a", "1"), ("module-b", "1")}
    assert directions[("H1.1", "module-a", "1")] == {"supports"}
    assert directions[("H1.1", "module-b", "1")] == {"opposes"}
    assert excluded == {("module-a", "2")}
