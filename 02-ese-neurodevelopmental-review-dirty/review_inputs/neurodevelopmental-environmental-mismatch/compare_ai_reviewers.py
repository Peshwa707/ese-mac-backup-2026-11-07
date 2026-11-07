#!/usr/bin/env python3
"""Compare Codex AI Reviewer 1 and blinded Claude AI Reviewer 2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
R1_RUN = ROOT / "runs" / "ndem-rapid-20260729"
R1_OUTPUT = (
    ROOT
    / "review_outputs"
    / "neurodevelopmental-environmental-mismatch-20260729"
)
DEFAULT_BLIND_ROOT = Path(
    "/Users/neurologic/Projects/ndem-claude-secondary-review-20260730"
)
DEFAULT_R2_OUTPUT = R1_OUTPUT / "claude_ai_reviewer_2"
MODULES = [
    ("ndem-a-persistence-compensation", "A"),
    ("ndem-b-overlap-social-dcd", "B"),
    ("ndem-c-context-motivation-strengths", "C"),
    ("ndem-d-external-scaffolding", "D"),
    ("ndem-e1-sleep-circadian", "E1"),
    ("ndem-e2-mood-substances", "E2"),
    ("ndem-f-daydreaming-rejection", "F"),
]
SCREEN_LABELS = ["exclude", "uncertain", "include"]
VERDICT_LABELS = [
    "contradicted",
    "unsupported",
    "indeterminate",
    "partially supported",
    "supported",
]
LEVEL_LABELS = ["very low", "low", "moderate", "high"]
ROLE_STATEMENT = (
    "Claude served as a blinded secondary AI reviewer under the same frozen "
    "rapid-review protocol. This is an AI-AI comparison, not Human Reviewer 2 "
    "screening, full-text adjudication, formal risk-of-bias/GRADE completion, "
    "treating-clinician sign-off, diagnosis, treatment guidance, or "
    "authorization to unlock conclusions. All disagreements remain pending "
    "human adjudication."
)
SCIENTIFIC_SEAL_STATUS = (
    "Claude blinded scientific judgments validated and sealed before Codex comparison"
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def verify_scientific_seal(blind_root: Path) -> dict[str, object]:
    blind_root = blind_root.resolve()
    seal_path = (
        blind_root / "provenance" / "claude_scientific_output_seal.json"
    )
    if not seal_path.is_file():
        raise FileNotFoundError(
            f"scientific seal required before comparison: {seal_path}"
        )
    seal = load_json(seal_path)
    if seal.get("status") != SCIENTIFIC_SEAL_STATUS:
        raise ValueError("scientific seal has an invalid status")
    gate_status = seal.get("gate_status") or {}
    if gate_status.get("secondary_ai_reviewer_claude") != "complete":
        raise ValueError("scientific seal does not mark AI Reviewer 2 complete")
    if gate_status.get("ai_ai_reconciliation") != "not started":
        raise ValueError(
            "scientific seal was not created before AI-AI reconciliation"
        )

    manifest_path = blind_root / "provenance" / "blind_input_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    if seal.get("blind_input_manifest_sha256") != sha256(manifest_path):
        raise ValueError("blind input manifest hash mismatch against scientific seal")

    entries = seal.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("scientific seal has no file manifest")
    sealed_paths: set[Path] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("scientific seal contains an invalid file entry")
        path = Path(str(entry.get("path") or "")).resolve()
        if not path.is_relative_to(blind_root):
            raise ValueError(f"sealed path escapes blind root: {path}")
        if path in sealed_paths:
            raise ValueError(f"duplicate path in scientific seal: {path}")
        sealed_paths.add(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if entry.get("bytes") != path.stat().st_size:
            raise ValueError(f"byte-size mismatch against scientific seal: {path}")
        if entry.get("sha256") != sha256(path):
            raise ValueError(f"hash mismatch against scientific seal: {path}")

    required_outputs = {
        blind_root
        / "outputs"
        / "modules"
        / module
        / filename
        for module, _ in MODULES
        for filename in (
            "screening_ai_r2_claude.json",
            "synthesis_ai_r2_claude.json",
        )
    }
    required_outputs.add(
        blind_root / "outputs" / "global_synthesis_ai_r2_claude.json"
    )
    missing = required_outputs - sealed_paths
    if missing:
        raise ValueError(
            "scientific outputs missing from seal: "
            + ", ".join(str(path) for path in sorted(missing, key=str))
        )
    return {
        "path": str(seal_path),
        "sha256": sha256(seal_path),
        "sealed_at_utc": seal.get("sealed_at_utc"),
        "verified_files": len(sealed_paths),
        "models_observed": seal.get("models_observed") or [],
        "transports_observed": seal.get("transports_observed") or [],
    }


def canonical(value: object) -> str:
    return str(value or "").strip().rstrip(".").casefold()


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def confusion(
    left: list[str], right: list[str], labels: list[str]
) -> dict[str, dict[str, int]]:
    matrix = {a: {b: 0 for b in labels} for a in labels}
    for a, b in zip(left, right, strict=True):
        matrix[a][b] += 1
    return matrix


def nominal_kappa(left: list[str], right: list[str], labels: list[str]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / n
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / n) * (right_counts[label] / n)
        for label in labels
    )
    if math.isclose(1.0 - expected, 0.0):
        return None
    return (observed - expected) / (1.0 - expected)


def linear_weighted_kappa(
    left: list[str], right: list[str], labels: list[str]
) -> float | None:
    if len(left) != len(right) or not left or len(labels) < 2:
        return None
    index = {label: idx for idx, label in enumerate(labels)}
    n = len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    observed_disagreement = sum(
        abs(index[a] - index[b]) / (len(labels) - 1)
        for a, b in zip(left, right, strict=True)
    ) / n
    expected_disagreement = sum(
        (left_counts[a] / n)
        * (right_counts[b] / n)
        * abs(index[a] - index[b])
        / (len(labels) - 1)
        for a in labels
        for b in labels
    )
    if math.isclose(expected_disagreement, 0.0):
        return None
    return 1.0 - observed_disagreement / expected_disagreement


def jaccard(left: set, right: set) -> float | None:
    union = left | right
    return len(left & right) / len(union) if union else None


def claim_annotation_metrics(
    rows: list[dict[str, object]],
) -> dict[str, object]:
    values = [
        float(row["claim_id_jaccard"])
        for row in rows
        if row["claim_id_jaccard"] is not None
    ]
    both_empty = sum(bool(row["claim_ids_both_empty"]) for row in rows)
    return {
        "claim_id_annotation_nonempty_union_pairs": len(values),
        "mean_claim_id_jaccard_nonempty_union": (
            statistics.fmean(values) if values else None
        ),
        "claim_id_annotation_both_empty_pairs": both_empty,
        "claim_id_annotation_both_empty_prevalence": safe_ratio(
            both_empty, len(rows)
        ),
    }


def screening_comparison(
    blind_root: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows: list[dict[str, object]] = []
    per_module: dict[str, object] = {}
    for module, code in MODULES:
        r1 = load_json(R1_RUN / module / "screening_ai_r1.json")
        r2 = load_json(
            blind_root
            / "outputs"
            / "modules"
            / module
            / "screening_ai_r2_claude.json"
        )
        if len(r1) != 140 or len(r2) != 140:
            raise ValueError(f"{module}: screening length mismatch")
        for position, (one, two) in enumerate(zip(r1, r2, strict=True), start=1):
            if str(one["pmid"]) != str(two["pmid"]):
                raise ValueError(f"{module}:{position}: paired PMID mismatch")
            left_claims = {str(value) for value in one["claim_ids"]}
            right_claims = {str(value) for value in two["claim_ids"]}
            rows.append(
                {
                    "module": module,
                    "module_code": code,
                    "occurrence_position": position,
                    "pmid": str(one["pmid"]),
                    "reviewer_1_decision": canonical(one["decision"]),
                    "reviewer_2_decision": canonical(two["decision"]),
                    "decision_agreement": canonical(one["decision"])
                    == canonical(two["decision"]),
                    "hard_include_exclude_conflict": {
                        canonical(one["decision"]),
                        canonical(two["decision"]),
                    }
                    == {"include", "exclude"},
                    "reviewer_1_priority": canonical(one["priority"]),
                    "reviewer_2_priority": canonical(two["priority"]),
                    "reviewer_1_evidence_role": canonical(one["evidence_role"]),
                    "reviewer_2_evidence_role": canonical(two["evidence_role"]),
                    "claim_id_jaccard": jaccard(left_claims, right_claims),
                    "claim_ids_both_empty": not left_claims and not right_claims,
                    "reviewer_1_reason": one["reason"],
                    "reviewer_2_reason": two["reason"],
                }
            )
        module_rows = [row for row in rows if row["module"] == module]
        left = [str(row["reviewer_1_decision"]) for row in module_rows]
        right = [str(row["reviewer_2_decision"]) for row in module_rows]
        advance_left = [
            "advance" if value in {"include", "uncertain"} else "exclude"
            for value in left
        ]
        advance_right = [
            "advance" if value in {"include", "uncertain"} else "exclude"
            for value in right
        ]
        per_module[module] = {
            "module_code": code,
            "n": len(module_rows),
            "reviewer_1_distribution": dict(Counter(left)),
            "reviewer_2_distribution": dict(Counter(right)),
            "exact_agreement": safe_ratio(
                sum(a == b for a, b in zip(left, right, strict=True)),
                len(left),
            ),
            "nominal_llm_llm_agreement_kappa": nominal_kappa(
                left, right, SCREEN_LABELS
            ),
            "linear_weighted_llm_llm_agreement_kappa": linear_weighted_kappa(
                left, right, SCREEN_LABELS
            ),
            "advance_vs_exclude_agreement": safe_ratio(
                sum(
                    a == b
                    for a, b in zip(advance_left, advance_right, strict=True)
                ),
                len(left),
            ),
            "hard_include_exclude_conflicts": sum(
                bool(row["hard_include_exclude_conflict"]) for row in module_rows
            ),
            **claim_annotation_metrics(module_rows),
            "confusion_matrix": confusion(left, right, SCREEN_LABELS),
        }

    left = [str(row["reviewer_1_decision"]) for row in rows]
    right = [str(row["reviewer_2_decision"]) for row in rows]
    advance_left = [
        "advance" if value in {"include", "uncertain"} else "exclude"
        for value in left
    ]
    advance_right = [
        "advance" if value in {"include", "uncertain"} else "exclude"
        for value in right
    ]
    pooled = {
        "n": len(rows),
        "primary_pairing_key": ["module", "occurrence_position"],
        "reviewer_1_distribution": dict(Counter(left)),
        "reviewer_2_distribution": dict(Counter(right)),
        "exact_agreement": safe_ratio(
            sum(a == b for a, b in zip(left, right, strict=True)), len(rows)
        ),
        "nominal_llm_llm_agreement_kappa": nominal_kappa(
            left, right, SCREEN_LABELS
        ),
        "linear_weighted_llm_llm_agreement_kappa": linear_weighted_kappa(
            left, right, SCREEN_LABELS
        ),
        "advance_vs_exclude_agreement": safe_ratio(
            sum(
                a == b
                for a, b in zip(advance_left, advance_right, strict=True)
            ),
            len(rows),
        ),
        "hard_include_exclude_conflicts": sum(
            bool(row["hard_include_exclude_conflict"]) for row in rows
        ),
        "any_disagreements": sum(not bool(row["decision_agreement"]) for row in rows),
        **claim_annotation_metrics(rows),
        "confusion_matrix": confusion(left, right, SCREEN_LABELS),
    }
    occurrences: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        occurrences[(str(row["module"]), str(row["pmid"]))].append(row)
    duplicate_occurrences = [
        {
            "module": module,
            "module_code": str(group[0]["module_code"]),
            "pmid": pmid,
            "occurrence_positions": [
                int(row["occurrence_position"]) for row in group
            ],
            "extra_occurrences": len(group) - 1,
        }
        for (module, pmid), group in occurrences.items()
        if len(group) > 1
    ]
    duplicate_occurrences.sort(
        key=lambda row: (str(row["module"]), str(row["pmid"]))
    )
    seen: set[tuple[str, str]] = set()
    deduped_rows: list[dict[str, object]] = []
    for row in rows:
        key = (str(row["module"]), str(row["pmid"]))
        if key not in seen:
            seen.add(key)
            deduped_rows.append(row)
    dedup_left = [str(row["reviewer_1_decision"]) for row in deduped_rows]
    dedup_right = [str(row["reviewer_2_decision"]) for row in deduped_rows]
    pooled["within_module_unique_pmid_sensitivity_analysis"] = {
        "n": len(deduped_rows),
        "pairing_key": ["module", "pmid"],
        "excluded_duplicate_occurrences": len(rows) - len(deduped_rows),
        "duplicate_occurrences": duplicate_occurrences,
        "exact_agreement": safe_ratio(
            sum(
                a == b
                for a, b in zip(dedup_left, dedup_right, strict=True)
            ),
            len(deduped_rows),
        ),
        "nominal_llm_llm_agreement_kappa": nominal_kappa(
            dedup_left, dedup_right, SCREEN_LABELS
        ),
    }
    if len(rows) == 980 and (
        len(deduped_rows) != 979
        or len(duplicate_occurrences) != 1
        or duplicate_occurrences[0]["module_code"] != "F"
        or duplicate_occurrences[0]["extra_occurrences"] != 1
    ):
        raise ValueError(
            "expected the frozen 980-occurrence corpus to have one duplicate "
            "occurrence in module F and 979 unique-within-module pairs"
        )
    return rows, {"pooled": pooled, "per_module": per_module}


def parse_reviewer_1_claims() -> dict[str, dict[str, str]]:
    text = (R1_OUTPUT / "systematic_evidence_review_full.md").read_text(
        encoding="utf-8"
    )
    claims: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        heading = re.match(r"^#{2,5}\s+((?:H|G)\d+(?:\.\d+|\.N\d+)?)\s+[—-]", line)
        if heading:
            current = heading.group(1)
            claims.setdefault(current, {})
            continue
        if not current:
            continue
        field = re.match(r"^- \*\*([^*]+):\*\*\s*(.+)$", line)
        if not field:
            continue
        name = canonical(field.group(1))
        value = canonical(field.group(2))
        if name == "verdict":
            claims[current]["verdict"] = value
        elif name == "certainty":
            claims[current]["certainty"] = value
        elif name == "directness to this case":
            claims[current]["directness_to_case"] = value
        elif name == "confidence that wording is appropriately calibrated":
            claims[current]["calibration_confidence"] = value
    if len(claims) != 66:
        raise ValueError(f"parsed {len(claims)} Reviewer-1 claims, expected 66")
    required = {
        "verdict",
        "certainty",
        "directness_to_case",
        "calibration_confidence",
    }
    for claim_id, row in claims.items():
        if set(row) != required:
            raise ValueError(f"{claim_id}: missing Reviewer-1 fields {required - set(row)}")
    return claims


def load_reviewer_2_claims(blind_root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for module, _ in MODULES:
        data = load_json(
            blind_root
            / "outputs"
            / "modules"
            / module
            / "synthesis_ai_r2_claude.json"
        )
        for row in data["claim_assessments"]:
            result[str(row["claim_id"])] = row
    global_data = load_json(
        blind_root / "outputs" / "global_synthesis_ai_r2_claude.json"
    )
    for row in global_data["claim_assessments"]:
        result[str(row["claim_id"])] = row
    if len(result) != 66:
        raise ValueError(f"loaded {len(result)} Reviewer-2 claims, expected 66")
    return result


def frozen_candidate_pmids(blind_root: Path) -> dict[str, set[str]]:
    return {
        module: {
            str(row["pmid"])
            for row in load_json(
                blind_root
                / "inputs"
                / "modules"
                / module
                / "screening_candidates.json"
            )
        }
        for module, _ in MODULES
    }


def evidence_sets(
    matrix_path: Path,
    frozen_candidates: dict[str, set[str]],
) -> tuple[
    dict[str, set[tuple[str, str]]],
    dict[tuple[str, str, str], set[str]],
    set[tuple[str, str]],
]:
    by_claim: dict[str, set[tuple[str, str]]] = defaultdict(set)
    directions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    excluded: set[tuple[str, str]] = set()
    with matrix_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            claim_id = str(row["claim_id"])
            module = str(row.get("source_module") or row["module"])
            pmid = str(row["pmid"])
            if not pmid:
                continue
            if pmid not in frozen_candidates.get(module, set()):
                excluded.add((module, pmid))
                continue
            occurrence = (module, pmid)
            by_claim[claim_id].add(occurrence)
            directions[(claim_id, module, pmid)].add(
                canonical(row["direction"])
            )
    return by_claim, directions, excluded


def extraction_selection(
    blind_root: Path,
) -> dict[str, object]:
    frozen_candidates = frozen_candidate_pmids(blind_root)
    per_module: dict[str, object] = {}
    r1_all: set[tuple[str, str]] = set()
    r2_all: set[tuple[str, str]] = set()
    excluded_r1: set[tuple[str, str]] = set()
    for module, code in MODULES:
        r1_unrestricted = {
            str(row["pmid"])
            for row in load_json(R1_RUN / module / "evidence_extraction_ai_r1.json")
        }
        r1 = r1_unrestricted & frozen_candidates[module]
        r1_outside = r1_unrestricted - frozen_candidates[module]
        excluded_r1 |= {(module, pmid) for pmid in r1_outside}
        r2 = {
            str(row["pmid"])
            for row in load_json(
                blind_root
                / "outputs"
                / "modules"
                / module
                / "synthesis_ai_r2_claude.json"
            )["extractions"]
        }
        r2_outside = r2 - frozen_candidates[module]
        if r2_outside:
            raise ValueError(
                f"{module}: Reviewer-2 selections outside frozen candidates: "
                f"{sorted(r2_outside)}"
            )
        r1_all |= {(module, pmid) for pmid in r1}
        r2_all |= {(module, pmid) for pmid in r2}
        per_module[module] = {
            "module_code": code,
            "reviewer_1_unique_pmids_before_frozen_candidate_filter": len(
                r1_unrestricted
            ),
            "reviewer_1_unique_pmids": len(r1),
            "reviewer_1_outside_frozen_candidate_pmids_excluded": sorted(
                r1_outside
            ),
            "reviewer_2_unique_pmids": len(r2),
            "shared_pmids": len(r1 & r2),
            "pmid_jaccard": jaccard(r1, r2),
        }
    return {
        "per_module": per_module,
        "pooled_module_pmid_occurrence_jaccard": jaccard(r1_all, r2_all),
        "reviewer_1_module_pmid_pairs": len(r1_all),
        "reviewer_1_outside_frozen_candidate_pairs_excluded": len(
            excluded_r1
        ),
        "reviewer_2_module_pmid_pairs": len(r2_all),
        "shared_module_pmid_pairs": len(r1_all & r2_all),
    }


def claim_comparison(
    blind_root: Path, r2_output: Path
) -> tuple[list[dict[str, object]], dict[str, object]]:
    r1 = parse_reviewer_1_claims()
    r2 = load_reviewer_2_claims(blind_root)
    frozen_candidates = frozen_candidate_pmids(blind_root)
    r1_evidence, r1_directions, r1_evidence_excluded = evidence_sets(
        R1_OUTPUT / "claim_evidence_matrix.csv", frozen_candidates
    )
    r2_evidence, r2_directions, r2_evidence_excluded = evidence_sets(
        r2_output / "claim_evidence_matrix.csv", frozen_candidates
    )
    if r2_evidence_excluded:
        raise ValueError(
            "Reviewer-2 claim evidence contains selections outside frozen "
            f"candidates: {sorted(r2_evidence_excluded)}"
        )
    rows: list[dict[str, object]] = []
    safety_prefixes = ("H7.", "H8.", "G6")
    for claim_id in r1:
        one = r1[claim_id]
        two = r2[claim_id]
        evidence_one = r1_evidence.get(claim_id, set())
        evidence_two = r2_evidence.get(claim_id, set())
        verdict_one = canonical(one["verdict"])
        verdict_two = canonical(two["verdict"])
        hard_verdict = (
            {verdict_one, verdict_two}
            & {"supported"}
            and {verdict_one, verdict_two}
            & {"unsupported", "contradicted"}
        )
        material = (
            verdict_one != verdict_two
            and (
                bool(hard_verdict)
                or claim_id.startswith(safety_prefixes)
                or one["certainty"] in {"high", "moderate"}
                or canonical(two["certainty"]) in {"high", "moderate"}
            )
        )
        rows.append(
            {
                "claim_id": claim_id,
                "reviewer_1_verdict": verdict_one,
                "reviewer_2_verdict": verdict_two,
                "verdict_agreement": verdict_one == verdict_two,
                "reviewer_1_certainty": canonical(one["certainty"]),
                "reviewer_2_certainty": canonical(two["certainty"]),
                "certainty_agreement": canonical(one["certainty"])
                == canonical(two["certainty"]),
                "reviewer_1_directness_to_case": canonical(
                    one["directness_to_case"]
                ),
                "reviewer_2_directness_to_case": canonical(
                    two["directness_to_case"]
                ),
                "directness_to_case_agreement": canonical(
                    one["directness_to_case"]
                )
                == canonical(two["directness_to_case"]),
                "reviewer_1_calibration_confidence": canonical(
                    one["calibration_confidence"]
                ),
                "reviewer_2_calibration_confidence": canonical(
                    two["calibration_confidence"]
                ),
                "calibration_confidence_agreement": canonical(
                    one["calibration_confidence"]
                )
                == canonical(two["calibration_confidence"]),
                "reviewer_1_evidence_module_pmid_occurrences": ";".join(
                    f"{module}:{pmid}"
                    for module, pmid in sorted(evidence_one)
                ),
                "reviewer_2_evidence_module_pmid_occurrences": ";".join(
                    f"{module}:{pmid}"
                    for module, pmid in sorted(evidence_two)
                ),
                "shared_evidence_module_pmid_occurrences": len(
                    evidence_one & evidence_two
                ),
                "evidence_occurrence_jaccard": jaccard(
                    evidence_one, evidence_two
                ),
                "evidence_occurrences_both_empty": (
                    not evidence_one and not evidence_two
                ),
                "material_disagreement": bool(material),
                "reviewer_2_calibrated_claim": two["calibrated_claim"],
            }
        )
    verdict_left = [str(row["reviewer_1_verdict"]) for row in rows]
    verdict_right = [str(row["reviewer_2_verdict"]) for row in rows]
    certainty_left = [str(row["reviewer_1_certainty"]) for row in rows]
    certainty_right = [str(row["reviewer_2_certainty"]) for row in rows]
    direct_left = [str(row["reviewer_1_directness_to_case"]) for row in rows]
    direct_right = [str(row["reviewer_2_directness_to_case"]) for row in rows]
    calibration_left = [
        str(row["reviewer_1_calibration_confidence"]) for row in rows
    ]
    calibration_right = [
        str(row["reviewer_2_calibration_confidence"]) for row in rows
    ]
    shared_direction_pairs = set(r1_directions) & set(r2_directions)
    direction_exact = sum(
        r1_directions[key] == r2_directions[key] for key in shared_direction_pairs
    )
    evidence_jaccards = [
        float(row["evidence_occurrence_jaccard"])
        for row in rows
        if row["evidence_occurrence_jaccard"] is not None
    ]
    evidence_both_empty = sum(
        bool(row["evidence_occurrences_both_empty"]) for row in rows
    )
    metrics = {
        "n_claims": len(rows),
        "verdict_exact_agreement": safe_ratio(
            sum(a == b for a, b in zip(verdict_left, verdict_right, strict=True)),
            len(rows),
        ),
        "verdict_nominal_llm_llm_agreement_kappa": nominal_kappa(
            verdict_left, verdict_right, VERDICT_LABELS
        ),
        "verdict_linear_weighted_llm_llm_agreement_kappa": linear_weighted_kappa(
            verdict_left, verdict_right, VERDICT_LABELS
        ),
        "verdict_confusion_matrix": confusion(
            verdict_left, verdict_right, VERDICT_LABELS
        ),
        "certainty_exact_agreement": safe_ratio(
            sum(
                a == b
                for a, b in zip(certainty_left, certainty_right, strict=True)
            ),
            len(rows),
        ),
        "certainty_linear_weighted_llm_llm_agreement_kappa": linear_weighted_kappa(
            certainty_left, certainty_right, LEVEL_LABELS
        ),
        "directness_to_case_exact_agreement": safe_ratio(
            sum(a == b for a, b in zip(direct_left, direct_right, strict=True)),
            len(rows),
        ),
        "directness_to_case_linear_weighted_llm_llm_agreement_kappa": linear_weighted_kappa(
            direct_left, direct_right, LEVEL_LABELS
        ),
        "calibration_confidence_exact_agreement": safe_ratio(
            sum(
                a == b
                for a, b in zip(
                    calibration_left, calibration_right, strict=True
                )
            ),
            len(rows),
        ),
        "calibration_confidence_linear_weighted_llm_llm_agreement_kappa": linear_weighted_kappa(
            calibration_left, calibration_right, LEVEL_LABELS
        ),
        "material_disagreements": sum(
            bool(row["material_disagreement"]) for row in rows
        ),
        "claim_evidence_nonempty_union_claims": len(evidence_jaccards),
        "mean_claim_evidence_occurrence_jaccard_nonempty_union": (
            statistics.fmean(evidence_jaccards)
            if evidence_jaccards
            else None
        ),
        "claim_evidence_both_empty_claims": evidence_both_empty,
        "claim_evidence_both_empty_prevalence": safe_ratio(
            evidence_both_empty, len(rows)
        ),
        "reviewer_1_evidence_module_pmid_pairs_excluded_outside_frozen_candidates": len(
            r1_evidence_excluded
        ),
        "shared_claim_module_pmid_pairs_with_direction": len(
            shared_direction_pairs
        ),
        "shared_claim_module_pmid_direction_exact_agreement": safe_ratio(
            direction_exact, len(shared_direction_pairs)
        ),
    }
    return rows, metrics


def percent(value: object) -> str:
    if value is None:
        return "NR"
    return f"{100 * float(value):.1f}%"


def number(value: object) -> str:
    if value is None:
        return "NR"
    return f"{float(value):.3f}"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    output: Path,
    scientific_seal: dict[str, object],
    screening: dict[str, object],
    selection: dict[str, object],
    claims: dict[str, object],
    claim_rows: list[dict[str, object]],
) -> Path:
    pooled = screening["pooled"]
    sensitivity = pooled["within_module_unique_pmid_sensitivity_analysis"]
    duplicate_text = "; ".join(
        f"{row['module_code']} PMID {row['pmid']} at positions "
        + "/".join(str(value) for value in row["occurrence_positions"])
        for row in sensitivity["duplicate_occurrences"]
    )
    material = [row for row in claim_rows if row["material_disagreement"]]
    lines = [
        "# Codex–Claude secondary AI reviewer comparison",
        "",
        f"> {ROLE_STATEMENT}",
        "",
        "## Capability conclusion",
        "",
        "The run demonstrates a blinded, cross-model secondary AI reviewer workflow on the same 980 ordered title/abstract occurrences and the same 66-claim protocol. It demonstrates reproducible AI-AI agreement measurement and disagreement routing. It does not demonstrate human-review equivalence, clinical validity, or adjudicated accuracy.",
        "",
        "- The pre-comparison scientific seal and every file in its manifest were verified; seal SHA-256: "
        f"`{scientific_seal['sha256']}`.",
        "- AI Reviewer 1 was produced in the OpenAI Codex task environment; the exact served OpenAI model identifier was not embedded in the pre-existing review artifacts and is therefore a provenance limitation.",
        "- AI Reviewer 2 provider logs record Anthropic `claude-opus-5` as the primary scientific model, with any auxiliary model usage retained separately.",
        "",
        "## Screening agreement",
        "",
        f"- Primary analysis: **{pooled['n']}** ordered occurrences paired by module and occurrence position.",
        f"- Unique-within-module sensitivity: **{sensitivity['n']}** module–PMID pairs after excluding "
        f"**{sensitivity['excluded_duplicate_occurrences']}** duplicate occurrence"
        f" ({duplicate_text or 'none'}).",
        f"- Three-way exact agreement: **{percent(pooled['exact_agreement'])}**.",
        "- Nominal `llm_llm_agreement` Cohen kappa: "
        f"**{number(pooled['nominal_llm_llm_agreement_kappa'])}**.",
        "- Linear-weighted `llm_llm_agreement` kappa: "
        f"**{number(pooled['linear_weighted_llm_llm_agreement_kappa'])}**.",
        "- Advance (include/uncertain) versus exclude agreement: "
        f"**{percent(pooled['advance_vs_exclude_agreement'])}**.",
        f"- Any three-way disagreements: **{pooled['any_disagreements']}**.",
        f"- Hard include-versus-exclude conflicts: **{pooled['hard_include_exclude_conflicts']}**.",
        "- Mean claim-ID annotation Jaccard among nonempty-union pairs "
        f"(n={pooled['claim_id_annotation_nonempty_union_pairs']}): "
        f"**{number(pooled['mean_claim_id_jaccard_nonempty_union'])}**.",
        "- Both reviewers supplied no claim-ID annotation for "
        f"**{pooled['claim_id_annotation_both_empty_pairs']}** pairs "
        f"({percent(pooled['claim_id_annotation_both_empty_prevalence'])}); "
        "these null/null pairs are not scored as perfect Jaccard agreement.",
        "- These are descriptive AI-AI metrics, not sensitivity, specificity, accuracy, or inter-rater reliability against a human reference standard.",
        "",
        "| Module | R1 I/U/E | R2 I/U/E | Exact | Nominal kappa | Hard conflicts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for module, code in MODULES:
        row = screening["per_module"][module]
        r1 = row["reviewer_1_distribution"]
        r2 = row["reviewer_2_distribution"]
        lines.append(
            f"| {code} | {r1.get('include', 0)}/{r1.get('uncertain', 0)}/{r1.get('exclude', 0)} "
            f"| {r2.get('include', 0)}/{r2.get('uncertain', 0)}/{r2.get('exclude', 0)} "
            f"| {percent(row['exact_agreement'])} "
            f"| {number(row['nominal_llm_llm_agreement_kappa'])} "
            f"| {row['hard_include_exclude_conflicts']} |"
        )
    lines.extend(
        (
            "",
            "## Evidence-selection overlap",
            "",
            f"- Reviewer-1 unique module–PMID pairs: {selection['reviewer_1_module_pmid_pairs']}.",
            f"- Reviewer-2 unique module–PMID pairs: {selection['reviewer_2_module_pmid_pairs']}.",
            f"- Shared module–PMID pairs: {selection['shared_module_pmid_pairs']}.",
            "- Pooled module–PMID Jaccard overlap: "
            f"{number(selection['pooled_module_pmid_occurrence_jaccard'])}.",
            "- Reviewer-1 module–PMID pairs excluded because they were outside the frozen candidate corpus: "
            f"{selection['reviewer_1_outside_frozen_candidate_pairs_excluded']}.",
            "",
            "## Claim-level agreement",
            "",
            f"- Claims paired: **{claims['n_claims']}**.",
            f"- Verdict exact agreement: **{percent(claims['verdict_exact_agreement'])}**.",
            "- Verdict nominal `llm_llm_agreement` kappa: "
            f"**{number(claims['verdict_nominal_llm_llm_agreement_kappa'])}**.",
            "- Verdict linear-weighted `llm_llm_agreement` kappa: "
            f"**{number(claims['verdict_linear_weighted_llm_llm_agreement_kappa'])}**.",
            f"- Certainty exact agreement: **{percent(claims['certainty_exact_agreement'])}**.",
            "- Directness-to-case exact agreement: "
            f"**{percent(claims['directness_to_case_exact_agreement'])}**.",
            "- Mean per-claim module–PMID occurrence Jaccard among nonempty-union claims "
            f"(n={claims['claim_evidence_nonempty_union_claims']}): "
            f"**{number(claims['mean_claim_evidence_occurrence_jaccard_nonempty_union'])}**.",
            "- Both reviewers had no frozen-corpus PMID evidence for "
            f"**{claims['claim_evidence_both_empty_claims']}** claims "
            f"({percent(claims['claim_evidence_both_empty_prevalence'])}).",
            f"- Material verdict disagreements routed to human adjudication: **{claims['material_disagreements']}**.",
            "",
            "## Material claim disagreements",
            "",
            "| Claim | Reviewer 1 | Claude Reviewer 2 | R1 module–PMID evidence | R2 module–PMID evidence |",
            "|---|---|---|---|---|",
        )
    )
    for row in material:
        lines.append(
            f"| {row['claim_id']} | {row['reviewer_1_verdict']} | "
            f"{row['reviewer_2_verdict']} | "
            f"{row['reviewer_1_evidence_module_pmid_occurrences'] or 'none'} | "
            f"{row['reviewer_2_evidence_module_pmid_occurrences'] or 'none'} |"
        )
    if not material:
        lines.append("| None | — | — | — | — |")
    lines.extend(
        (
            "",
            "## Interpretation boundaries",
            "",
            "- Agreement shows reproducibility between two AI-family review passes under frozen inputs; disagreement identifies adjudication workload.",
            "- Neither reviewer is a reference standard. Concordant error and shared-corpus bias remain possible.",
            "- Abstract-only judgments may change after full-text review and design-specific risk-of-bias assessment.",
            "- Human Reviewer 2, clinical sign-off, and official engine export remain pending.",
            "",
            "## Gate status",
            "",
            "```json",
            json.dumps(
                {
                    "secondary_ai_reviewer_claude": "complete",
                    "ai_ai_reconciliation": "draft",
                    "human_reviewer_2": "pending",
                    "full_text_screening": "pending",
                    "formal_risk_of_bias": "pending",
                    "clinical_sign_off": "pending",
                    "official_engine_export": "withheld",
                },
                indent=2,
            ),
            "```",
        )
    )
    path = output / "ai_reviewer_comparison.md"
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-root", type=Path, default=DEFAULT_BLIND_ROOT)
    parser.add_argument("--r2-output", type=Path, default=DEFAULT_R2_OUTPUT)
    parser.add_argument("--output", type=Path, default=R1_OUTPUT / "ai_ai_comparison")
    args = parser.parse_args()
    blind_root = args.blind_root.resolve()
    r2_output = args.r2_output.resolve()
    output = args.output.resolve()
    scientific_seal = verify_scientific_seal(blind_root)
    output.mkdir(parents=True, exist_ok=True)

    screening_rows, screening_metrics = screening_comparison(blind_root)
    selection_metrics = extraction_selection(blind_root)
    claim_rows, claim_metrics = claim_comparison(blind_root, r2_output)

    disagreements = [
        row for row in screening_rows if not bool(row["decision_agreement"])
    ]
    screening_path = output / "screening_disagreements.csv"
    claim_path = output / "claim_level_comparison.csv"
    write_csv(screening_path, disagreements)
    write_csv(claim_path, claim_rows)
    metrics = {
        "status": {
            "secondary_ai_reviewer_claude": "complete",
            "ai_ai_reconciliation": "draft",
            "human_reviewer_2": "pending",
            "full_text_screening": "pending",
            "formal_risk_of_bias": "pending",
            "clinical_sign_off": "pending",
            "official_engine_export": "withheld",
        },
        "role_statement": ROLE_STATEMENT,
        "scientific_seal": scientific_seal,
        "screening": screening_metrics,
        "evidence_selection": selection_metrics,
        "claims": claim_metrics,
    }
    metrics_path = output / "comparison_metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path = write_report(
        output,
        scientific_seal,
        screening_metrics,
        selection_metrics,
        claim_metrics,
        claim_rows,
    )
    source_paths = [
        Path(str(scientific_seal["path"])),
        Path(__file__).resolve(),
        R1_OUTPUT / "systematic_evidence_review_full.md",
        R1_OUTPUT / "claim_evidence_matrix.csv",
        r2_output / "claim_evidence_matrix.csv",
        *[
            R1_RUN / module / filename
            for module, _ in MODULES
            for filename in (
                "screening_ai_r1.json",
                "evidence_extraction_ai_r1.json",
            )
        ],
    ]
    for path in source_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "role_statement": ROLE_STATEMENT,
        "scientific_seal_sha256": scientific_seal["sha256"],
        "sources": [file_record(path) for path in source_paths],
        "outputs": [
            file_record(path)
            for path in (
                report_path,
                metrics_path,
                screening_path,
                claim_path,
            )
        ],
        "gate_status": metrics["status"],
    }
    provenance_path = output / "comparison_provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "screening_pairs": len(screening_rows),
                "screening_disagreements": len(disagreements),
                "claims": len(claim_rows),
                "material_claim_disagreements": claim_metrics[
                    "material_disagreements"
                ],
                "scientific_seal_sha256": scientific_seal["sha256"],
                "report": str(report_path),
                "metrics": str(metrics_path),
                "provenance": str(provenance_path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
