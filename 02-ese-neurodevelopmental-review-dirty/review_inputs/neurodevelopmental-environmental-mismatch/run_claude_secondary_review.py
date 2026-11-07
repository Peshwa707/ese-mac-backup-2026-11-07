#!/usr/bin/env python3
"""Run Claude as a blinded secondary AI reviewer on one module or globally."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BLIND_ROOT = Path(
    "/Users/neurologic/Projects/ndem-claude-secondary-review-20260730"
)
MODULES = {
    "ndem-a-persistence-compensation": "A",
    "ndem-b-overlap-social-dcd": "B",
    "ndem-c-context-motivation-strengths": "C",
    "ndem-d-external-scaffolding": "D",
    "ndem-e1-sleep-circadian": "E1",
    "ndem-e2-mood-substances": "E2",
    "ndem-f-daydreaming-rejection": "F",
}
DECISIONS = {"include", "exclude", "uncertain"}
PRIORITIES = {"high", "moderate", "low"}
EVIDENCE_ROLES = {"supporting", "opposing", "qualifying", "neutral"}
VERDICTS = {
    "supported",
    "partially supported",
    "unsupported",
    "contradicted",
    "indeterminate",
}
LEVELS = {"high", "moderate", "low", "very low"}
EVIDENCE_DIRECTNESS = {
    "direct",
    "indirect",
    "highly indirect",
    "no eligible direct evidence",
}
EXTRACTION_DIRECTNESS = {"direct", "indirect", "highly indirect"}
EXTRACTION_DIRECTIONS = {"supports", "opposes", "mixed", "neutral"}
CLAUDE_COMMAND = "/Users/neurologic/.local/bin/claude"
API_MODEL_ALIASES = {"opus": "claude-opus-5"}
API_INPUT_USD_PER_MTOK = {"claude-opus-5": 5.0}
API_OUTPUT_USD_PER_MTOK = {"claude-opus-5": 25.0}
API_MAX_OUTPUT_TOKENS = 64000
API_PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
API_PRICING_CHECKED = "2026-07-30"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def claims_for(blind_root: Path, module_code: str) -> list[dict[str, str]]:
    with (blind_root / "inputs" / "claim_ledger.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        return [
            row for row in csv.DictReader(handle) if row["module"] == module_code
        ]


def json_only(text: str):
    value = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.S)
    if fenced:
        value = fenced.group(1).strip()
    return json.loads(value)


def assessment_schema(claim_ids: list[str]) -> dict[str, object]:
    properties: dict[str, object] = {
        "claim_id": {"type": "string", "enum": claim_ids},
        "verdict": {"type": "string", "enum": sorted(VERDICTS)},
        "certainty": {"type": "string", "enum": sorted(LEVELS)},
        "evidence_directness": {
            "type": "string",
            "enum": sorted(EVIDENCE_DIRECTNESS),
        },
        "directness_to_case": {"type": "string", "enum": sorted(LEVELS)},
        "calibration_confidence": {"type": "string", "enum": sorted(LEVELS)},
        "strongest_support": {"type": "string", "minLength": 1, "maxLength": 900},
        "strongest_opposition": {"type": "string", "minLength": 1, "maxLength": 900},
        "qualified_interpretation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 900,
        },
        "alternative_explanations": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "methodological_weaknesses": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "calibrated_claim": {"type": "string", "minLength": 1, "maxLength": 700},
        "falsifier": {"type": "string", "minLength": 1, "maxLength": 700},
        "personal_or_clinical_data": {
            "type": "string",
            "minLength": 1,
            "maxLength": 700,
        },
        "supporting_pmids": {
            "type": "array",
            "maxItems": 14,
            "items": {"type": "string"},
        },
        "opposing_pmids": {
            "type": "array",
            "maxItems": 14,
            "items": {"type": "string"},
        },
        "qualifying_pmids": {
            "type": "array",
            "maxItems": 14,
            "items": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def screening_schema(
    blind_root: Path, module: str
) -> dict[str, object]:
    claim_ids = [
        row["claim_id"] for row in claims_for(blind_root, MODULES[module])
    ]
    properties: dict[str, object] = {
        "pmid": {"type": "string"},
        "module": {"type": "string", "const": module},
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "reason": {"type": "string", "minLength": 1, "maxLength": 240},
        "claim_ids": {
            "type": "array",
            "items": {"type": "string", "enum": claim_ids},
        },
        "evidence_role": {"type": "string", "enum": sorted(EVIDENCE_ROLES)},
        "priority": {"type": "string", "enum": sorted(PRIORITIES)},
        "abstract_available": {"type": "boolean", "const": True},
        "fulltext_required": {"type": "boolean"},
    }
    screening_array: dict[str, object] = {
        "type": "array",
        "minItems": 140,
        "maxItems": 140,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }
    return {
        "type": "object",
        "properties": {"screening": screening_array},
        "required": ["screening"],
        "additionalProperties": False,
    }


def synthesis_schema(
    blind_root: Path, module: str
) -> dict[str, object]:
    output_dir = blind_root / "outputs" / "modules" / module
    screening = load_json(output_dir / "screening_ai_r2_claude.json")
    extraction_count = min(
        14,
        sum(row["decision"] in {"include", "uncertain"} for row in screening),
    )
    claim_ids = [
        row["claim_id"] for row in claims_for(blind_root, MODULES[module])
    ]
    extraction_properties: dict[str, object] = {
        "pmid": {"type": "string"},
        "claim_ids": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": claim_ids},
        },
        "direction": {
            "type": "string",
            "enum": sorted(EXTRACTION_DIRECTIONS),
        },
        "evidence_role": {"type": "string", "enum": sorted(EVIDENCE_ROLES)},
        "study_design": {"type": "string", "minLength": 1, "maxLength": 240},
        "population": {"type": "string", "minLength": 1, "maxLength": 500},
        "exposure_intervention": {
            "type": "string",
            "minLength": 1,
            "maxLength": 400,
        },
        "comparator": {"type": "string", "minLength": 1, "maxLength": 400},
        "outcome": {"type": "string", "minLength": 1, "maxLength": 400},
        "main_result": {"type": "string", "minLength": 1, "maxLength": 900},
        "effect_estimate": {"type": "string", "minLength": 1, "maxLength": 500},
        "directness": {
            "type": "string",
            "enum": sorted(EXTRACTION_DIRECTNESS),
        },
        "major_limitations": {
            "type": "string",
            "minLength": 1,
            "maxLength": 700,
        },
        "individual_applicability": {
            "type": "string",
            "minLength": 1,
            "maxLength": 600,
        },
        "provisional_quality": {"type": "string", "enum": sorted(LEVELS)},
        "quality_reason": {"type": "string", "minLength": 1, "maxLength": 500},
        "evidence_basis": {"type": "string", "const": "abstract"},
        "abstract_available": {"type": "boolean", "const": True},
        "fulltext_required": {"type": "boolean", "const": True},
    }
    properties: dict[str, object] = {
        "extractions": {
            "type": "array",
            "minItems": extraction_count,
            "maxItems": extraction_count,
            "items": {
                "type": "object",
                "properties": extraction_properties,
                "required": list(extraction_properties),
                "additionalProperties": False,
            },
        },
        "claim_assessments": {
            "type": "array",
            "minItems": len(claim_ids),
            "maxItems": len(claim_ids),
            "items": assessment_schema(claim_ids),
        },
        "module_summary": {"type": "string", "minLength": 1, "maxLength": 1500},
        "review_limitations": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def global_schema(blind_root: Path) -> dict[str, object]:
    claim_ids = [row["claim_id"] for row in claims_for(blind_root, "G")]
    properties: dict[str, object] = {
        "claim_assessments": {
            "type": "array",
            "minItems": len(claim_ids),
            "maxItems": len(claim_ids),
            "items": assessment_schema(claim_ids),
        },
        "global_null_hypothesis": {
            "type": "string",
            "enum": ["rejected", "not rejected", "indeterminate"],
        },
        "overall_synthesis": {"type": "string", "minLength": 1, "maxLength": 2000},
        "safety_relevant_considerations": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "minLength": 1, "maxLength": 350},
        },
        "review_limitations": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "minLength": 1, "maxLength": 350},
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def shared_prefix(blind_root: Path) -> str:
    return "\n\n".join(
        (
            "# SOURCE PROTOCOL — UNALTERED\n" + load_text(
                blind_root / "inputs" / "source_protocol.md"
            ),
            "# ORIGINAL USER REVIEW PROMPT — UNALTERED\n" + load_text(
                blind_root / "inputs" / "original_prompt.txt"
            ),
            "# SECONDARY AI REVIEWER CONTRACT\n" + load_text(
                blind_root / "REVIEWER_CONTRACT.md"
            ),
        )
    )


def screening_prompt(blind_root: Path, module: str) -> str:
    module_dir = blind_root / "inputs" / "modules" / module
    claims = claims_for(blind_root, MODULES[module])
    candidates = load_json(module_dir / "screening_candidates.json")
    return f"""{shared_prefix(blind_root)}

# EXECUTION TASK: BLINDED TITLE/ABSTRACT SCREENING

You are Claude acting as independent secondary AI reviewer for module
`{module}`. You are an AI reviewer, not a human Reviewer 2 or clinician.

Apply the source protocol and original prompt to every one of the 140 records
below, in the supplied order. Inspect the abstract, not only the title. The
shared search and rapid-review cap are frozen, so do not add outside sources.
Search explicitly within this set for support, opposition, null findings, and
qualifications. Use concise, record-specific reasons.

Return ONLY one raw JSON object with exactly one top-level field named
`screening`. Its value must be an array containing exactly 140 objects in
exactly the candidate PMID order. Each object must contain exactly these fields:

- `pmid`: string
- `module`: exactly `{module}`
- `decision`: `include`, `exclude`, or `uncertain`
- `reason`: nonempty string, at most 30 words
- `claim_ids`: array using only the module claim IDs below; empty if irrelevant
- `evidence_role`: `supporting`, `opposing`, `qualifying`, or `neutral`
- `priority`: `high`, `moderate`, or `low`
- `abstract_available`: true
- `fulltext_required`: true for include/uncertain, otherwise false

Do not return Markdown, commentary, citations outside the JSON, or a summary.

# MODULE CLAIMS
{json.dumps(claims, indent=2, ensure_ascii=False)}

# MODULE SPECIFICATION
{json.dumps(load_json(module_dir / "spec.json"), indent=2, ensure_ascii=False)}

# ORDERED FROZEN SCREENING RECORDS
{json.dumps(candidates, indent=2, ensure_ascii=False)}
"""


def synthesis_prompt(blind_root: Path, module: str) -> str:
    module_dir = blind_root / "inputs" / "modules" / module
    output_dir = blind_root / "outputs" / "modules" / module
    claims = claims_for(blind_root, MODULES[module])
    candidates = load_json(module_dir / "screening_candidates.json")
    screening = load_json(output_dir / "screening_ai_r2_claude.json")
    eligible = [
        row for row in screening if row["decision"] in {"include", "uncertain"}
    ]
    return f"""{shared_prefix(blind_root)}

# EXECUTION TASK: BLINDED EXTRACTION AND CLAIM SYNTHESIS

You are Claude acting as independent secondary AI reviewer for module
`{module}`. You are an AI reviewer, not a human Reviewer 2 or clinician. Your
own screening decisions are supplied below. You have not seen Codex decisions.

Select exactly {min(14, len(eligible))} highest-value records from your
include/uncertain set, balancing evidence hierarchy, directness, support,
opposition, and qualification across all module claims. Use only facts stated
in the supplied abstracts. If an effect size, sample size, comparator, or other
detail is absent, write `NR`; never infer it. Every extracted record remains
abstract-level and requires full text. Keep every narrative field concise:
normally one or two sentences, with no repeated background.

Return ONLY one raw JSON object with exactly these top-level fields:

1. `extractions`: array of exactly {min(14, len(eligible))} objects. Each object
   must contain exactly:
   `pmid`, `claim_ids`, `direction`, `evidence_role`, `study_design`,
   `population`, `exposure_intervention`, `comparator`, `outcome`,
   `main_result`, `effect_estimate`, `directness`, `major_limitations`,
   `individual_applicability`, `provisional_quality`, `quality_reason`,
   `evidence_basis`, `abstract_available`, `fulltext_required`.
   Enumerations: direction = supports/opposes/mixed/neutral; evidence_role =
   supporting/opposing/qualifying/neutral; directness =
   direct/indirect/highly indirect; provisional_quality =
   high/moderate/low/very low. Set evidence_basis=`abstract`,
   abstract_available=true, fulltext_required=true.

2. `claim_assessments`: exactly one object for every claim below, in the same
   order. Each object must contain exactly:
   `claim_id`, `verdict`, `certainty`, `evidence_directness`,
   `directness_to_case`, `calibration_confidence`, `strongest_support`,
   `strongest_opposition`, `qualified_interpretation`,
   `alternative_explanations`, `methodological_weaknesses`,
   `calibrated_claim`, `falsifier`, `personal_or_clinical_data`,
   `supporting_pmids`, `opposing_pmids`, `qualifying_pmids`.
   verdict = supported/partially supported/unsupported/contradicted/indeterminate.
   certainty, directness_to_case, and calibration_confidence =
   high/moderate/low/very low. evidence_directness =
   direct/indirect/highly indirect/no eligible direct evidence.
   The three PMID fields are arrays of strings and may be empty.
   Every PMID in those three arrays MUST also appear among your selected
   `extractions`. If a claim needs a different record, include that record in
   the fixed-size extraction set and displace a lower-value record. Do not cite
   any merely screened, excluded, or outside PMID in a claim assessment.

3. `module_summary`: concise balanced synthesis, no diagnosis or treatment.
4. `review_limitations`: array of concise limitations, including abstract-only
   evidence, absent human Reviewer 2, and absent clinical sign-off.

Do not return Markdown fences or any text outside the JSON.

# MODULE CLAIMS
{json.dumps(claims, indent=2, ensure_ascii=False)}

# MODULE SPECIFICATION
{json.dumps(load_json(module_dir / "spec.json"), indent=2, ensure_ascii=False)}

# ORDERED FROZEN SCREENING RECORDS WITH ABSTRACTS
{json.dumps(candidates, indent=2, ensure_ascii=False)}

# YOUR BLINDED SCREENING DECISIONS
{json.dumps(screening, indent=2, ensure_ascii=False)}
"""


def global_prompt(blind_root: Path) -> str:
    global_claims = claims_for(blind_root, "G")
    module_results = {
        module: load_json(
            blind_root
            / "outputs"
            / "modules"
            / module
            / "synthesis_ai_r2_claude.json"
        )
        for module in MODULES
    }
    return f"""{shared_prefix(blind_root)}

# EXECUTION TASK: BLINDED GLOBAL MODEL AND NULL-HYPOTHESIS SYNTHESIS

You are Claude acting as independent secondary AI reviewer. You are an AI
reviewer, not a human Reviewer 2 or clinician. Use only your seven module
outputs below. You have not seen any Codex decision or conclusion.
Keep each narrative field concise and avoid repeating the same limitation.

Return ONLY one raw JSON object with exactly:

- `claim_assessments`: exactly one assessment for each G1-G6 claim in order,
  using exactly the same assessment fields and enumerations specified for the
  module claim assessments.
- `global_null_hypothesis`: `rejected`, `not rejected`, or `indeterminate`.
- `overall_synthesis`: balanced concise synthesis of strongest, weakest, and
  unresolved components.
- `safety_relevant_considerations`: array limited to evidence-review cautions,
  not diagnosis or treatment directives.
- `review_limitations`: array including abstract-only evidence, rapid cap,
  unavailable databases, absent human Reviewer 2, and absent clinical sign-off.

Do not return Markdown fences or any text outside the JSON.

# GLOBAL CLAIMS
{json.dumps(global_claims, indent=2, ensure_ascii=False)}

# CLAUDE MODULE OUTPUTS
{json.dumps(module_results, indent=2, ensure_ascii=False)}
"""


def validate_screening(
    data, blind_root: Path, module: str
) -> list[dict[str, object]]:
    if not isinstance(data, list):
        raise TypeError("screening output is not an array")
    candidates = load_json(
        blind_root
        / "inputs"
        / "modules"
        / module
        / "screening_candidates.json"
    )
    claims = {row["claim_id"] for row in claims_for(blind_root, MODULES[module])}
    required = {
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
    if len(data) != len(candidates):
        raise ValueError(f"expected {len(candidates)} screening rows, got {len(data)}")
    if [str(row.get("pmid")) for row in data] != [
        str(row["pmid"]) for row in candidates
    ]:
        raise ValueError("screening PMID order differs from candidate order")
    for row in data:
        if set(row) != required:
            raise ValueError(f"{row.get('pmid')}: screening fields differ")
        if row["module"] != module:
            raise ValueError(f"{row['pmid']}: wrong module")
        if row["decision"] not in DECISIONS:
            raise ValueError(f"{row['pmid']}: invalid decision")
        if row["priority"] not in PRIORITIES:
            raise ValueError(f"{row['pmid']}: invalid priority")
        if row["evidence_role"] not in EVIDENCE_ROLES:
            raise ValueError(f"{row['pmid']}: invalid evidence role")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError(f"{row['pmid']}: empty reason")
        if not isinstance(row["claim_ids"], list) or not set(row["claim_ids"]) <= claims:
            raise ValueError(f"{row['pmid']}: invalid claim IDs")
        if row["abstract_available"] is not True:
            raise ValueError(f"{row['pmid']}: abstract not acknowledged")
        expected_fulltext = row["decision"] in {"include", "uncertain"}
        if row["fulltext_required"] is not expected_fulltext:
            raise ValueError(f"{row['pmid']}: invalid full-text flag")
    return data


def validate_assessment(row: dict[str, object], valid_claims: set[str]) -> None:
    required = {
        "claim_id",
        "verdict",
        "certainty",
        "evidence_directness",
        "directness_to_case",
        "calibration_confidence",
        "strongest_support",
        "strongest_opposition",
        "qualified_interpretation",
        "alternative_explanations",
        "methodological_weaknesses",
        "calibrated_claim",
        "falsifier",
        "personal_or_clinical_data",
        "supporting_pmids",
        "opposing_pmids",
        "qualifying_pmids",
    }
    if set(row) != required:
        raise ValueError(f"{row.get('claim_id')}: claim-assessment fields differ")
    if row["claim_id"] not in valid_claims:
        raise ValueError(f"{row['claim_id']}: invalid claim ID")
    if row["verdict"] not in VERDICTS:
        raise ValueError(f"{row['claim_id']}: invalid verdict")
    if row["certainty"] not in LEVELS:
        raise ValueError(f"{row['claim_id']}: invalid certainty")
    if row["directness_to_case"] not in LEVELS:
        raise ValueError(f"{row['claim_id']}: invalid case directness")
    if row["calibration_confidence"] not in LEVELS:
        raise ValueError(f"{row['claim_id']}: invalid calibration confidence")
    if row["evidence_directness"] not in EVIDENCE_DIRECTNESS:
        raise ValueError(f"{row['claim_id']}: invalid evidence directness")
    for field in (
        "strongest_support",
        "strongest_opposition",
        "qualified_interpretation",
        "calibrated_claim",
        "falsifier",
        "personal_or_clinical_data",
    ):
        if not isinstance(row[field], str) or not str(row[field]).strip():
            raise ValueError(f"{row['claim_id']}: empty {field}")
    for field in (
        "alternative_explanations",
        "methodological_weaknesses",
        "supporting_pmids",
        "opposing_pmids",
        "qualifying_pmids",
    ):
        if not isinstance(row[field], list):
            raise ValueError(f"{row['claim_id']}: {field} is not an array")
    for field in ("alternative_explanations", "methodological_weaknesses"):
        if len(row[field]) > 5:
            raise ValueError(f"{row['claim_id']}: too many {field}")
    for field in ("supporting_pmids", "opposing_pmids", "qualifying_pmids"):
        if len(row[field]) > 14:
            raise ValueError(f"{row['claim_id']}: too many {field}")


def validate_synthesis(
    data, blind_root: Path, module: str
) -> dict[str, object]:
    if not isinstance(data, dict) or set(data) != {
        "extractions",
        "claim_assessments",
        "module_summary",
        "review_limitations",
    }:
        raise ValueError("invalid synthesis top-level fields")
    output_dir = blind_root / "outputs" / "modules" / module
    screening = load_json(output_dir / "screening_ai_r2_claude.json")
    eligible = {
        str(row["pmid"])
        for row in screening
        if row["decision"] in {"include", "uncertain"}
    }
    valid_claims_order = [
        row["claim_id"] for row in claims_for(blind_root, MODULES[module])
    ]
    valid_claims = set(valid_claims_order)
    extractions = data["extractions"]
    if len(extractions) != min(14, len(eligible)):
        raise ValueError("wrong extraction count")
    extraction_required = {
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
    pmids: list[str] = []
    for row in extractions:
        if set(row) != extraction_required:
            raise ValueError(f"{row.get('pmid')}: extraction fields differ")
        pmid = str(row["pmid"])
        if pmid not in eligible:
            raise ValueError(f"{pmid}: extraction not eligible after screening")
        if pmid in pmids:
            raise ValueError(f"{pmid}: duplicate extraction")
        pmids.append(pmid)
        if not row["claim_ids"] or not set(row["claim_ids"]) <= valid_claims:
            raise ValueError(f"{pmid}: invalid extraction claim IDs")
        if row["direction"] not in EXTRACTION_DIRECTIONS:
            raise ValueError(f"{pmid}: invalid direction")
        if row["evidence_role"] not in EVIDENCE_ROLES:
            raise ValueError(f"{pmid}: invalid evidence role")
        if row["directness"] not in EXTRACTION_DIRECTNESS:
            raise ValueError(f"{pmid}: invalid directness")
        if row["provisional_quality"] not in LEVELS:
            raise ValueError(f"{pmid}: invalid quality")
        if row["evidence_basis"] != "abstract":
            raise ValueError(f"{pmid}: non-abstract evidence basis")
        if row["abstract_available"] is not True or row["fulltext_required"] is not True:
            raise ValueError(f"{pmid}: invalid evidence/full-text flags")
    assessments = data["claim_assessments"]
    if [row.get("claim_id") for row in assessments] != valid_claims_order:
        raise ValueError("claim assessments missing or out of ledger order")
    for row in assessments:
        validate_assessment(row, valid_claims)
        cited = {
            str(pmid)
            for field in ("supporting_pmids", "opposing_pmids", "qualifying_pmids")
            for pmid in row[field]
        }
        if not cited <= set(pmids):
            raise ValueError(f"{row['claim_id']}: cites unextracted PMID")
    if not isinstance(data["module_summary"], str) or not data["module_summary"].strip():
        raise ValueError("empty module summary")
    if not isinstance(data["review_limitations"], list):
        raise ValueError("review limitations is not an array")
    if len(data["review_limitations"]) > 8:
        raise ValueError("too many review limitations")
    return data


def validate_global(data, blind_root: Path) -> dict[str, object]:
    if not isinstance(data, dict) or set(data) != {
        "claim_assessments",
        "global_null_hypothesis",
        "overall_synthesis",
        "safety_relevant_considerations",
        "review_limitations",
    }:
        raise ValueError("invalid global top-level fields")
    claim_order = [row["claim_id"] for row in claims_for(blind_root, "G")]
    if [row.get("claim_id") for row in data["claim_assessments"]] != claim_order:
        raise ValueError("global claims missing or out of order")
    all_selected = {
        str(row["pmid"])
        for module in MODULES
        for row in load_json(
            blind_root
            / "outputs"
            / "modules"
            / module
            / "synthesis_ai_r2_claude.json"
        )["extractions"]
    }
    for row in data["claim_assessments"]:
        validate_assessment(row, set(claim_order))
        cited = {
            str(pmid)
            for field in ("supporting_pmids", "opposing_pmids", "qualifying_pmids")
            for pmid in row[field]
        }
        if not cited <= all_selected:
            raise ValueError(f"{row['claim_id']}: cites unextracted PMID")
    if data["global_null_hypothesis"] not in {
        "rejected",
        "not rejected",
        "indeterminate",
    }:
        raise ValueError("invalid global null verdict")
    if len(data["safety_relevant_considerations"]) > 10:
        raise ValueError("too many safety-relevant considerations")
    if len(data["review_limitations"]) > 10:
        raise ValueError("too many global review limitations")
    return data


def run_claude(
    prompt: str,
    blind_root: Path,
    model: str,
    effort: str,
    max_budget_usd: float,
    schema: dict[str, object],
) -> dict[str, object]:
    command = [
        CLAUDE_COMMAND,
        "-p",
        "--safe-mode",
        "--setting-sources",
        "project,local",
        "--model",
        model,
        "--effort",
        effort,
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-chrome",
        "--disable-slash-commands",
        "--prompt-suggestions",
        "false",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        "--no-session-persistence",
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    process = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=blind_root,
        env=os.environ.copy(),
        timeout=900,
        check=False,
    )
    if process.returncode != 0:
        details = process.stderr[-4000:] or process.stdout[-4000:]
        raise RuntimeError(
            f"Claude exited {process.returncode}: {details}"
        )
    outer = json.loads(process.stdout)
    if outer.get("is_error"):
        raise RuntimeError(f"Claude API error: {outer}")
    if not isinstance(outer.get("result"), str):
        raise RuntimeError("Claude response has no text result")
    if outer.get("permission_denials"):
        raise RuntimeError(f"Claude reported permission denials: {outer['permission_denials']}")
    server_use = (outer.get("usage") or {}).get("server_tool_use") or {}
    if int(server_use.get("web_search_requests") or 0) != 0:
        raise RuntimeError("Claude used web search in a frozen-corpus stage")
    if int(server_use.get("web_fetch_requests") or 0) != 0:
        raise RuntimeError("Claude used web fetch in a frozen-corpus stage")
    model_usage = outer.get("modelUsage") or {}
    if model == "opus" and not any("opus" in name for name in model_usage):
        raise RuntimeError(f"requested Opus but provider usage was {sorted(model_usage)}")
    return outer


def run_claude_api(
    prompt: str,
    model: str,
    effort: str,
    max_budget_usd: float,
    schema: dict[str, object],
) -> dict[str, object]:
    """Run the identical frozen prompt/schema through Anthropic's native API.

    This is a transport fallback for Claude Max service instability. It uses the
    project's hardened credential reader, sends no tools, streams the response
    to avoid long non-streaming timeouts, and returns a CLI-compatible envelope
    so the same validators and provenance seal remain authoritative.
    """
    import anthropic

    from core.llm.gateway import load_key

    key = load_key("anthropic")
    if key is None:
        raise RuntimeError("Anthropic API credential is not configured")
    requested_model = API_MODEL_ALIASES.get(model, model)
    client = anthropic.Anthropic(
        api_key=key.reveal(),
        max_retries=5,
        timeout=1200.0,
    )
    provider_schema = api_compatible_schema(schema)
    output_config = {
        "effort": effort,
        "format": {"type": "json_schema", "schema": provider_schema},
    }
    token_count = client.messages.count_tokens(
        model=requested_model,
        thinking={"type": "adaptive"},
        output_config=output_config,
        messages=[{"role": "user", "content": prompt}],
    )
    if requested_model not in API_INPUT_USD_PER_MTOK:
        raise RuntimeError(
            f"no reviewed API price configured for {requested_model}"
        )
    maximum_token_cost_usd = (
        token_count.input_tokens
        * API_INPUT_USD_PER_MTOK[requested_model]
        / 1_000_000
        + API_MAX_OUTPUT_TOKENS
        * API_OUTPUT_USD_PER_MTOK[requested_model]
        / 1_000_000
    )
    if maximum_token_cost_usd > max_budget_usd:
        raise RuntimeError(
            "API token-cost ceiling "
            f"${maximum_token_cost_usd:.6f} exceeds budget "
            f"${max_budget_usd:.6f}"
        )
    with client.messages.stream(
        model=requested_model,
        max_tokens=API_MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        output_config=output_config,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()
    if message.model != requested_model:
        raise RuntimeError(
            f"requested {requested_model}, provider returned {message.model}"
        )
    if message.stop_reason != "end_turn":
        raise RuntimeError(
            f"Claude API terminal reason was {message.stop_reason!r}"
        )
    result = "".join(
        block.text for block in message.content if block.type == "text"
    )
    if not result.strip():
        raise RuntimeError("Claude API response has no text result")
    structured = json_only(result)
    usage = message.usage.model_dump(mode="json")
    usage["server_tool_use"] = {
        "web_search_requests": 0,
        "web_fetch_requests": 0,
    }
    cache_creation = usage.get("cache_creation") or {}
    calculated_cost_usd = (
        int(usage.get("input_tokens") or 0)
        * API_INPUT_USD_PER_MTOK[requested_model]
        / 1_000_000
        + int(usage.get("output_tokens") or 0)
        * API_OUTPUT_USD_PER_MTOK[requested_model]
        / 1_000_000
        + int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
        * API_INPUT_USD_PER_MTOK[requested_model]
        * 1.25
        / 1_000_000
        + int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
        * API_INPUT_USD_PER_MTOK[requested_model]
        * 2.0
        / 1_000_000
        + int(usage.get("cache_read_input_tokens") or 0)
        * API_INPUT_USD_PER_MTOK[requested_model]
        * 0.10
        / 1_000_000
    )
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "terminal_reason": "completed",
        "stop_reason": message.stop_reason,
        "permission_denials": [],
        "result": result,
        "structured_output": structured,
        "response_id": message.id,
        "modelUsage": {
            message.model: {
                "inputTokens": usage.get("input_tokens", 0),
                "outputTokens": usage.get("output_tokens", 0),
                "cacheReadInputTokens": usage.get(
                    "cache_read_input_tokens", 0
                ),
                "cacheCreationInputTokens": usage.get(
                    "cache_creation_input_tokens", 0
                ),
                "webSearchRequests": 0,
                "costUSD": None,
                "canonicalModel": message.model,
                "provider": "anthropic_api",
            }
        },
        "usage": usage,
        "total_cost_usd": None,
        "calculated_cost_usd": round(calculated_cost_usd, 6),
        "budget_preflight": {
            "budget_usd": max_budget_usd,
            "counted_input_tokens": token_count.input_tokens,
            "max_output_tokens": API_MAX_OUTPUT_TOKENS,
            "maximum_token_cost_usd": round(maximum_token_cost_usd, 6),
            "pricing_source": API_PRICING_SOURCE,
            "pricing_checked": API_PRICING_CHECKED,
            "billing_cutoff": False,
        },
        "transport": "anthropic_api",
        "anthropic_sdk_version": anthropic.__version__,
    }


def api_compatible_schema(value):
    """Return Anthropic's supported schema subset without weakening validation.

    Anthropic structured output accepts array ``minItems`` only at 0 or 1 and
    does not accept ``maxItems``. The provider therefore receives minItems=1
    with maxItems omitted. The post-response validators enforce every exact
    count, order, and protocol-specific maximum before any output is saved.
    """
    if isinstance(value, dict):
        transformed = {}
        for key, item in value.items():
            if key == "minItems" and isinstance(item, int) and item > 1:
                transformed[key] = 1
            elif key == "maxItems":
                continue
            else:
                transformed[key] = api_compatible_schema(item)
        return transformed
    if isinstance(value, list):
        return [api_compatible_schema(item) for item in value]
    return value


def canonicalize_screening_order(
    payload, blind_root: Path, module: str
) -> tuple[object, dict[str, object]]:
    """Canonicalize a pure row-order deviation without changing judgments."""
    rows = payload.get("screening") if isinstance(payload, dict) else payload
    candidates = load_json(
        blind_root
        / "inputs"
        / "modules"
        / module
        / "screening_candidates.json"
    )
    expected = [str(row["pmid"]) for row in candidates]
    if not isinstance(rows, list):
        return payload, {"applied": False, "reason": "not_an_array"}
    observed = [str(row.get("pmid")) for row in rows]
    if observed == expected:
        return rows, {"applied": False, "reason": "already_in_frozen_order"}
    if (
        len(observed) != len(expected)
        or len(set(observed)) != len(observed)
        or set(observed) != set(expected)
    ):
        return rows, {
            "applied": False,
            "reason": "not_a_pure_unique_row_order_deviation",
        }
    by_pmid = {str(row["pmid"]): row for row in rows}
    reordered = [by_pmid[pmid] for pmid in expected]
    moved_positions = sum(
        left != right for left, right in zip(observed, expected)
    )
    return reordered, {
        "applied": True,
        "reason": "pure unique PMID row-order canonicalization",
        "moved_positions": moved_positions,
        "judgment_fields_changed": 0,
        "candidate_set_changed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-root", type=Path, default=DEFAULT_BLIND_ROOT)
    parser.add_argument("--stage", choices=("screen", "synthesize", "global"), required=True)
    parser.add_argument("--module", choices=tuple(MODULES))
    parser.add_argument("--model", default="opus")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--max-budget-usd", type=float, default=5.0)
    parser.add_argument(
        "--transport",
        choices=("cli", "api"),
        default="cli",
        help="Claude transport; api uses the hardened project credential path",
    )
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument(
        "--resume-raw",
        type=Path,
        help=(
            "Finalize a preserved provider envelope without another provider "
            "call; currently supported only for screening"
        ),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    blind_root = args.blind_root.resolve()
    if not (blind_root / "provenance" / "blind_input_manifest.json").is_file():
        raise FileNotFoundError("blind packet manifest missing")
    if args.stage != "global" and not args.module:
        parser.error("--module is required for screen/synthesize")
    if args.stage == "global" and args.module:
        parser.error("--module is not valid for global stage")
    if args.resume_raw and args.stage != "screen":
        parser.error("--resume-raw is supported only for screening")

    if args.stage == "screen":
        prompt = screening_prompt(blind_root, args.module)
        output_dir = blind_root / "outputs" / "modules" / args.module
        parsed_path = output_dir / "screening_ai_r2_claude.json"
        log_path = output_dir / f"claude_{args.transport}_screen.json"
        prompt_path = blind_root / "prompts" / f"{args.module}_screen.md"
        validator = lambda value: validate_screening(
            value["screening"] if isinstance(value, dict) else value,
            blind_root,
            args.module,
        )
        schema = screening_schema(blind_root, args.module)
    elif args.stage == "synthesize":
        prompt = synthesis_prompt(blind_root, args.module)
        output_dir = blind_root / "outputs" / "modules" / args.module
        parsed_path = output_dir / "synthesis_ai_r2_claude.json"
        log_path = output_dir / f"claude_{args.transport}_synthesize.json"
        prompt_path = blind_root / "prompts" / f"{args.module}_synthesize.md"
        validator = lambda value: validate_synthesis(value, blind_root, args.module)
        schema = synthesis_schema(blind_root, args.module)
    else:
        prompt = global_prompt(blind_root)
        output_dir = blind_root / "outputs"
        parsed_path = output_dir / "global_synthesis_ai_r2_claude.json"
        log_path = output_dir / f"claude_{args.transport}_global.json"
        prompt_path = blind_root / "prompts" / "global_synthesis.md"
        validator = lambda value: validate_global(value, blind_root)
        schema = global_schema(blind_root)

    output_dir.mkdir(parents=True, exist_ok=True)
    if not args.force and (parsed_path.exists() or log_path.exists()):
        raise FileExistsError(f"refusing to overwrite existing stage output: {parsed_path}")
    prompt_path = prompt_path.with_name(
        f"{prompt_path.stem}_attempt_{args.attempt:02d}{prompt_path.suffix}"
    )
    raw_path = output_dir / (
        f"claude_{args.transport}_{args.stage}_attempt_"
        f"{args.attempt:02d}_raw.json"
    )
    if not args.force and raw_path.exists() and not args.resume_raw:
        raise FileExistsError(f"refusing to overwrite raw provider output: {raw_path}")
    if prompt_path.exists():
        if load_text(prompt_path) != prompt:
            raise ValueError(
                f"existing attempt prompt differs from live frozen prompt: "
                f"{prompt_path}"
            )
    else:
        if args.resume_raw:
            raise FileNotFoundError(
                f"resume requires the preserved attempt prompt: {prompt_path}"
            )
        prompt_path.write_text(prompt, encoding="utf-8")
    started = datetime.now(timezone.utc).isoformat()
    if args.resume_raw:
        resume_path = args.resume_raw.resolve()
        if not resume_path.is_relative_to(blind_root):
            raise ValueError("--resume-raw must be inside the blind review root")
        if resume_path != raw_path.resolve():
            raise ValueError(
                "--resume-raw must match the stage/transport/attempt raw path"
            )
        outer = load_json(resume_path)
    else:
        if args.transport == "api":
            outer = run_claude_api(
                prompt,
                args.model,
                args.effort,
                args.max_budget_usd,
                schema,
            )
        else:
            outer = run_claude(
                prompt,
                blind_root,
                args.model,
                args.effort,
                args.max_budget_usd,
                schema,
            )
        raw_path.write_text(
            json.dumps(outer, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    payload = outer.get("structured_output")
    if payload is None:
        payload = json_only(str(outer["result"]))
    normalization = {
        "applied": False,
        "reason": "not_a_screening_stage",
    }
    if args.stage == "screen":
        payload, normalization = canonicalize_screening_order(
            payload, blind_root, args.module
        )
    parsed = validator(payload)
    parsed_path.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    outer["execution_provenance"] = {
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_model": args.model,
        "resolved_model": (
            API_MODEL_ALIASES.get(args.model, args.model)
            if args.transport == "api"
            else None
        ),
        "transport": args.transport,
        "effort": args.effort,
        "max_budget_usd": (
            None
            if args.resume_raw and outer.get("budget_preflight") is None
            else args.max_budget_usd
        ),
        "budget_enforcement": (
            "Claude CLI hard agent budget"
            if args.transport == "cli"
            else (
                "preflight token-cost ceiling; not an account billing cutoff"
                if outer.get("budget_preflight") is not None
                else None
            )
        ),
        "attempt": args.attempt,
        "resumed_from_preserved_raw": bool(args.resume_raw),
        "deterministic_normalization": normalization,
        "safe_mode": True,
        "setting_sources": ["project", "local"],
        "permission_mode": "plan",
        "tools": [],
        "strict_empty_mcp": True,
        "chrome": False,
        "slash_commands": False,
        "prompt_suggestions": False,
        "session_persistence": False,
        "prompt_path": str(prompt_path),
        "prompt_sha256": sha256(prompt_path),
        "schema_sha256": hashlib.sha256(
            json.dumps(
                schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "provider_schema_sha256": (
            hashlib.sha256(
                json.dumps(
                    api_compatible_schema(schema),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if args.transport == "api"
            else None
        ),
        "provider_schema_note": (
            "Anthropic supports array minItems only at 0 or 1 and not "
            "maxItems; exact counts, order, and maxima remain enforced by "
            "the local validator"
            if args.transport == "api"
            else None
        ),
        "raw_provider_output_path": str(raw_path),
        "raw_provider_output_sha256": sha256(raw_path),
        "parsed_output_path": str(parsed_path),
        "parsed_output_sha256": sha256(parsed_path),
    }
    log_path.write_text(
        json.dumps(outer, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "stage": args.stage,
                "module": args.module,
                "model_usage": sorted((outer.get("modelUsage") or {}).keys()),
                "cost_usd": (
                    outer.get("total_cost_usd")
                    if outer.get("total_cost_usd") is not None
                    else outer.get("calculated_cost_usd")
                ),
                "parsed_output": str(parsed_path),
                "sha256": sha256(parsed_path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Claude Reviewer-2 run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
