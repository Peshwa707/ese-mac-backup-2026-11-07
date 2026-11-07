#!/usr/bin/env python3
"""Build Claude AI Reviewer-2 review artifacts from sealed structured outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fetch_selected_pubmed import fetch_xml, parse_article, resolve_doi


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BLIND_ROOT = Path(
    "/Users/neurologic/Projects/ndem-claude-secondary-review-20260730"
)
DEFAULT_OUTPUT = (
    ROOT
    / "review_outputs"
    / "neurodevelopmental-environmental-mismatch-20260729"
    / "claude_ai_reviewer_2"
)
MODULES = [
    (
        "ndem-a-persistence-compensation",
        "A",
        "Developmental persistence, executive function, and compensation",
    ),
    (
        "ndem-b-overlap-social-dcd",
        "B",
        "ADHD-autism-DCD overlap and social functioning",
    ),
    (
        "ndem-c-context-motivation-strengths",
        "C",
        "Context, motivation, environmental fit, and proposed strengths",
    ),
    ("ndem-d-external-scaffolding", "D", "External cognitive scaffolding"),
    ("ndem-e1-sleep-circadian", "E1", "Sleep and circadian modifiers"),
    (
        "ndem-e2-mood-substances",
        "E2",
        "Mood, substances, and medication state effects",
    ),
    (
        "ndem-f-daydreaming-rejection",
        "F",
        "Daydreaming, rejection, anxiety, and emotional compensation",
    ),
]
TITLE = (
    "Adaptive-Constraint and Environmental-Mismatch Model of Adult "
    "Neurodevelopmental Functioning"
)
ROLE_STATEMENT = (
    "Claude served as a blinded secondary AI reviewer under the same frozen "
    "rapid-review protocol. This is an AI-AI comparison input, not Human "
    "Reviewer 2 screening, full-text adjudication, formal risk-of-bias/GRADE "
    "completion, treating-clinician sign-off, diagnosis, treatment guidance, "
    "or authorization to unlock conclusions. All disagreements remain pending "
    "human adjudication."
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def _resolve_declared_path(
    blind_root: Path, raw_path: object, context: str
) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{context}: missing path")
    declared = Path(raw_path)
    if not declared.is_absolute():
        declared = blind_root / declared
    try:
        resolved = declared.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{context}: sealed file is missing: {declared}") from exc
    try:
        resolved.relative_to(blind_root)
    except ValueError as exc:
        raise ValueError(f"{context}: path escapes blind root: {resolved}") from exc
    if not resolved.is_file():
        raise ValueError(f"{context}: expected a regular file: {resolved}")
    return resolved


def _verify_hashed_entries(
    blind_root: Path,
    entries: object,
    path_field: str,
    context: str,
) -> dict[Path, dict[str, object]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{context}: no hashed files recorded")
    verified: dict[Path, dict[str, object]] = {}
    for index, entry in enumerate(entries):
        item_context = f"{context}[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{item_context}: invalid file record")
        path = _resolve_declared_path(
            blind_root, entry.get(path_field), item_context
        )
        if path in verified:
            raise ValueError(f"{item_context}: duplicate sealed path: {path}")
        expected_bytes = entry.get("bytes")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ValueError(f"{item_context}: invalid byte count")
        if path.stat().st_size != expected_bytes:
            raise ValueError(f"{item_context}: byte count mismatch for {path}")
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or sha256(path) != expected_hash:
            raise ValueError(f"{item_context}: SHA-256 mismatch for {path}")
        verified[path] = entry
    return verified


def verify_scientific_seal(
    blind_root: Path,
) -> tuple[Path, dict[str, object], dict[str, int]]:
    """Verify the sealed output chain before any build write or network access."""
    blind_root = blind_root.resolve()
    manifest_path = blind_root / "provenance" / "blind_input_manifest.json"
    seal_path = (
        blind_root / "provenance" / "claude_scientific_output_seal.json"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"blind input manifest is missing: {manifest_path}")
    if not seal_path.is_file():
        raise FileNotFoundError(
            "scientific output seal is missing; seal Claude outputs before build: "
            f"{seal_path}"
        )

    manifest = load_json(manifest_path)
    seal = load_json(seal_path)
    if not isinstance(manifest, dict) or not isinstance(seal, dict):
        raise ValueError("manifest and scientific seal must be JSON objects")
    manifest_hash = sha256(manifest_path)
    if seal.get("blind_input_manifest_sha256") != manifest_hash:
        raise ValueError("scientific seal does not match the blind input manifest")
    gate_status = seal.get("gate_status")
    if not isinstance(gate_status, dict) or gate_status.get(
        "secondary_ai_reviewer_claude"
    ) != "complete":
        raise ValueError("scientific seal does not mark Claude review complete")

    manifest_files = _verify_hashed_entries(
        blind_root,
        manifest.get("files"),
        "packet_path",
        "blind_input_manifest.files",
    )
    sealed_files = _verify_hashed_entries(
        blind_root,
        seal.get("files"),
        "path",
        "claude_scientific_output_seal.files",
    )

    required_manifest_paths = {
        (blind_root / "inputs" / "claim_ledger.csv").resolve(),
        (blind_root / "inputs" / "grounding_summary.json").resolve(),
        (blind_root / "inputs" / "screening_scope_summary.json").resolve(),
    }
    required_sealed_paths = {
        (blind_root / "outputs" / "global_synthesis_ai_r2_claude.json").resolve()
    }
    for module, _, _ in MODULES:
        module_input = blind_root / "inputs" / "modules" / module
        required_manifest_paths.update(
            {
                (module_input / "screening_candidates.json").resolve(),
                (module_input / "search_execution.json").resolve(),
                (module_input / "search_manifest.json").resolve(),
                (module_input / "corpus.json").resolve(),
            }
        )
        module_output = blind_root / "outputs" / "modules" / module
        required_sealed_paths.update(
            {
                (module_output / "screening_ai_r2_claude.json").resolve(),
                (module_output / "synthesis_ai_r2_claude.json").resolve(),
            }
        )
    missing_manifest = sorted(required_manifest_paths - set(manifest_files), key=str)
    if missing_manifest:
        raise ValueError(
            "blind manifest does not seal required builder inputs: "
            + ", ".join(str(path) for path in missing_manifest)
        )
    missing_outputs = sorted(required_sealed_paths - set(sealed_files), key=str)
    if missing_outputs:
        raise ValueError(
            "scientific seal does not cover required builder outputs: "
            + ", ".join(str(path) for path in missing_outputs)
        )
    return (
        seal_path,
        seal,
        {
            "manifest_files_verified": len(manifest_files),
            "sealed_files_verified": len(sealed_files),
        },
    )


def normalized(text: str) -> str:
    return re.sub(r"\W+", "", str(text or "").casefold())


def read_claims(blind_root: Path) -> list[dict[str, str]]:
    with (blind_root / "inputs" / "claim_ledger.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        return list(csv.DictReader(handle))


def load_reviewer_outputs(
    blind_root: Path,
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, dict[str, str]],
]:
    claims = read_claims(blind_root)
    claim_by_id = {row["claim_id"]: row for row in claims}
    module_outputs: dict[str, dict[str, object]] = {}
    assessments: dict[str, dict[str, object]] = {}
    for module, code, _ in MODULES:
        path = (
            blind_root
            / "outputs"
            / "modules"
            / module
            / "synthesis_ai_r2_claude.json"
        )
        result = load_json(path)
        module_outputs[module] = result
        expected = [row["claim_id"] for row in claims if row["module"] == code]
        actual = [row["claim_id"] for row in result["claim_assessments"]]
        if actual != expected:
            raise ValueError(f"{module}: claim order or coverage mismatch")
        for row in result["claim_assessments"]:
            assessments[row["claim_id"]] = row
    global_output = load_json(
        blind_root / "outputs" / "global_synthesis_ai_r2_claude.json"
    )
    expected_global = [
        row["claim_id"] for row in claims if row["module"] == "G"
    ]
    if [
        row["claim_id"] for row in global_output["claim_assessments"]
    ] != expected_global:
        raise ValueError("global claim order or coverage mismatch")
    for row in global_output["claim_assessments"]:
        assessments[row["claim_id"]] = row
    if set(assessments) != set(claim_by_id):
        raise ValueError("Claude output does not cover all 66 claims")
    return module_outputs, global_output, claim_by_id


def fetch_metadata(
    blind_root: Path,
    output: Path,
    module_outputs: dict[str, dict[str, object]],
    resolve_dois: bool,
) -> list[dict[str, object]]:
    selected_pmids = sorted(
        {
            str(row["pmid"])
            for result in module_outputs.values()
            for row in result["extractions"]
        },
        key=int,
    )
    chunks = [
        selected_pmids[index : index + 150]
        for index in range(0, len(selected_pmids), 150)
    ]
    combined_root = ET.Element("PubmedArticleSet")
    for index, chunk in enumerate(chunks):
        if index:
            time.sleep(0.4)
        parsed = ET.fromstring(fetch_xml(chunk))
        for article in parsed.findall("PubmedArticle"):
            combined_root.append(article)
    xml_bytes = ET.tostring(
        combined_root, encoding="utf-8", xml_declaration=True
    )
    (output / "selected_pubmed_efetch.xml").write_bytes(xml_bytes)
    records = [
        parse_article(article)
        for article in combined_root.findall("PubmedArticle")
    ]
    by_pmid = {str(row["pmid"]): row for row in records}
    missing = sorted(set(selected_pmids) - set(by_pmid), key=int)
    if missing:
        raise ValueError(f"selected PMIDs missing from PubMed EFetch: {missing}")

    for module, _, _ in MODULES:
        candidates = {
            str(row["pmid"]): row
            for row in load_json(
                blind_root
                / "inputs"
                / "modules"
                / module
                / "screening_candidates.json"
            )
        }
        for extraction in module_outputs[module]["extractions"]:
            pmid = str(extraction["pmid"])
            if pmid not in candidates:
                raise ValueError(f"{module}: selected PMID outside frozen candidates")
            if normalized(candidates[pmid]["title"]) != normalized(
                str(by_pmid[pmid]["title"])
            ):
                raise ValueError(f"{module}: PubMed title mismatch for {pmid}")
            if not str(candidates[pmid]["abstract"]).strip():
                raise ValueError(f"{module}: selected PMID lacks inspected abstract")

    doi_results: dict[str, dict[str, object]] = {}
    dois = sorted({str(row["doi"]) for row in records if row.get("doi")})
    if resolve_dois:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(resolve_doi, doi): doi for doi in dois}
            for future in as_completed(futures):
                doi_results[futures[future]] = future.result()
    for row in records:
        row["doi_verification"] = doi_results.get(
            str(row.get("doi") or ""),
            {
                "status": "not_run" if row.get("doi") else "not_applicable",
                "http_status": None,
                "final_url": "",
            },
        )
    records.sort(key=lambda row: int(str(row["pmid"])))
    metadata_path = output / "selected_pubmed_metadata.json"
    metadata_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return records


def citation(record: dict[str, object]) -> str:
    authors = list(record.get("authors") or [])
    if not authors:
        author_text = "Unknown author"
    elif len(authors) <= 6:
        author_text = ", ".join(str(author) for author in authors)
    else:
        author_text = ", ".join(str(author) for author in authors[:6]) + ", et al"
    title = str(record.get("title") or "").strip()
    if title and not title.endswith((".", "?", "!")):
        title += "."
    journal = str(record.get("journal") or "")
    year = str(record.get("year") or "")
    volume = str(record.get("volume") or "")
    issue = str(record.get("issue") or "")
    pages = str(record.get("pages_or_article_number") or "")
    locator = volume + (f"({issue})" if issue else "")
    if pages:
        locator += f":{pages}" if locator else pages
    value = f"{author_text}. {title} {journal}. {year}"
    if locator:
        value += f";{locator}"
    value += "."
    if record.get("doi"):
        value += f" doi:{record['doi']}."
    value += f" PMID:{record['pmid']}."
    return re.sub(r"\s+", " ", value).strip()


def screening_counts(
    blind_root: Path,
) -> tuple[dict[str, Counter[str]], Counter[str]]:
    by_module: dict[str, Counter[str]] = {}
    total: Counter[str] = Counter()
    for module, _, _ in MODULES:
        rows = load_json(
            blind_root
            / "outputs"
            / "modules"
            / module
            / "screening_ai_r2_claude.json"
        )
        if len(rows) != 140:
            raise ValueError(f"{module}: screening count is not 140")
        counts = Counter(str(row["decision"]) for row in rows)
        by_module[module] = counts
        total.update(counts)
    if sum(total.values()) != 980:
        raise ValueError("Claude screening total is not 980")
    return by_module, total


def write_verified_references(
    output: Path, metadata: list[dict[str, object]]
) -> Path:
    path = output / "verified_references.csv"
    fields = [
        "reference_id",
        "citation",
        "title",
        "authors",
        "journal",
        "year",
        "volume",
        "issue",
        "pages_or_article_number",
        "doi",
        "pmid",
        "pmcid",
        "pubmed_url",
        "doi_url",
        "verification_source",
        "doi_verification_status",
        "retraction_or_correction_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, row in enumerate(metadata, start=1):
            writer.writerow(
                {
                    "reference_id": f"R{index:03d}",
                    "citation": citation(row),
                    "title": row["title"],
                    "authors": row["author_string"],
                    "journal": row["journal"],
                    "year": row["year"],
                    "volume": row["volume"],
                    "issue": row["issue"],
                    "pages_or_article_number": row[
                        "pages_or_article_number"
                    ],
                    "doi": row["doi"],
                    "pmid": row["pmid"],
                    "pmcid": row["pmcid"],
                    "pubmed_url": row["pubmed_url"],
                    "doi_url": row["doi_url"],
                    "verification_source": row["verification_source"],
                    "doi_verification_status": row["doi_verification"]["status"],
                    "retraction_or_correction_status": row[
                        "retraction_or_correction_status"
                    ],
                }
            )
    return path


def assessment_relationships(
    assessment: dict[str, object],
) -> list[tuple[str, str, str]]:
    relationships: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    relationship_fields = (
        ("supporting_pmids", "supports", "supporting"),
        ("opposing_pmids", "opposes", "opposing"),
        ("qualifying_pmids", "mixed", "qualifying"),
    )
    for field, direction, role in relationship_fields:
        values = assessment.get(field)
        if not isinstance(values, list):
            raise ValueError(
                f"{assessment.get('claim_id')}: {field} is not an array"
            )
        for value in values:
            pmid = str(value)
            if not pmid:
                raise ValueError(
                    f"{assessment.get('claim_id')}: empty PMID in {field}"
                )
            key = (pmid, role)
            if key in seen:
                raise ValueError(
                    f"{assessment.get('claim_id')}: duplicate PMID {pmid} "
                    f"in {field}"
                )
            seen.add(key)
            relationships.append((pmid, direction, role))
    return relationships


def write_matrix(
    output: Path,
    module_outputs: dict[str, dict[str, object]],
    global_output: dict[str, object],
    metadata: list[dict[str, object]],
) -> Path:
    path = output / "claim_evidence_matrix.csv"
    meta = {str(row["pmid"]): row for row in metadata}
    fields = [
        "claim_id",
        "module",
        "source_module",
        "pmid",
        "citation",
        "doi",
        "pubmed_url",
        "verification",
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
        "fulltext_required",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        extraction_by_key: dict[
            tuple[str, str], dict[str, object]
        ] = {}
        extraction_keys_by_pmid: dict[
            str, list[tuple[str, str]]
        ] = defaultdict(list)
        for module, result in module_outputs.items():
            for row in result["extractions"]:
                pmid = str(row["pmid"])
                key = (module, pmid)
                if key in extraction_by_key:
                    raise ValueError(
                        f"{module}: duplicate extraction PMID {pmid}"
                    )
                if pmid not in meta:
                    raise ValueError(f"{module}: metadata missing for PMID {pmid}")
                extraction_by_key[key] = row
                extraction_keys_by_pmid[pmid].append(key)

        def write_relationship(
            claim_id: object,
            claim_module: str,
            source_module: str,
            pmid: str,
            direction: str,
            role: str,
            row: dict[str, object],
        ) -> None:
            record = meta[pmid]
            writer.writerow(
                {
                    "claim_id": claim_id,
                    "module": claim_module,
                    "source_module": source_module,
                    "pmid": pmid,
                    "citation": citation(record),
                    "doi": record["doi"],
                    "pubmed_url": record["pubmed_url"],
                    "verification": "NCBI PubMed EFetch",
                    "direction": direction,
                    "evidence_role": role,
                    **{
                        field: row[field]
                        for field in fields
                        if field in row
                        and field not in {"direction", "evidence_role"}
                    },
                }
            )

        for module, result in module_outputs.items():
            for assessment in result["claim_assessments"]:
                for pmid, direction, role in assessment_relationships(
                    assessment
                ):
                    key = (module, pmid)
                    if key not in extraction_by_key:
                        raise ValueError(
                            f"{assessment['claim_id']}: cited PMID {pmid} "
                            f"lacks an extraction in {module}"
                        )
                    write_relationship(
                        assessment["claim_id"],
                        module,
                        module,
                        pmid,
                        direction,
                        role,
                        extraction_by_key[key],
                    )

        for assessment in global_output["claim_assessments"]:
            for pmid, direction, role in assessment_relationships(
                assessment
            ):
                keys = extraction_keys_by_pmid.get(pmid, [])
                if not keys:
                    raise ValueError(
                        f"{assessment['claim_id']}: global PMID {pmid} "
                        "lacks an extraction"
                    )
                for source_module, _ in keys:
                    row = extraction_by_key[(source_module, pmid)]
                    write_relationship(
                        assessment["claim_id"],
                        "global",
                        source_module,
                        pmid,
                        direction,
                        role,
                        row,
                    )
    return path


def format_pmids(values: list[object]) -> str:
    return ", ".join(f"PMID {value}" for value in values) if values else "None"


def claim_markdown(
    claim: dict[str, str], assessment: dict[str, object]
) -> str:
    alternatives = "; ".join(
        str(item) for item in assessment["alternative_explanations"]
    ) or "None specified"
    weaknesses = "; ".join(
        str(item) for item in assessment["methodological_weaknesses"]
    ) or "None specified"
    return "\n".join(
        (
            f"### {claim['claim_id']} — {claim['claim_text']}",
            "",
            f"- **Verdict:** {str(assessment['verdict']).title()}",
            f"- **Certainty:** {str(assessment['certainty']).title()}",
            f"- **Evidence directness:** {str(assessment['evidence_directness']).title()}",
            f"- **Directness to this case:** {str(assessment['directness_to_case']).title()}",
            "- **Confidence that wording is appropriately calibrated:** "
            f"{str(assessment['calibration_confidence']).title()}",
            f"- **Strongest support:** {assessment['strongest_support']} "
            f"({format_pmids(assessment['supporting_pmids'])})",
            f"- **Strongest opposition:** {assessment['strongest_opposition']} "
            f"({format_pmids(assessment['opposing_pmids'])})",
            f"- **Qualified interpretation:** {assessment['qualified_interpretation']} "
            f"({format_pmids(assessment['qualifying_pmids'])})",
            "- **Alternative explanations (unverified hypotheses unless cited):** "
            f"{alternatives}",
            f"- **Methodological weaknesses:** {weaknesses}",
            f"- **Calibrated claim:** {assessment['calibrated_claim']}",
            f"- **Falsifier:** {assessment['falsifier']}",
            f"- **Personal or clinical data needed:** "
            f"{assessment['personal_or_clinical_data']}",
        )
    )


def prisma_flow_counts(blind_root: Path) -> dict[str, int]:
    screening_scope = load_json(
        blind_root / "inputs" / "screening_scope_summary.json"
    )
    grounding_summary = load_json(
        blind_root / "inputs" / "grounding_summary.json"
    )
    if not isinstance(screening_scope, list) or not isinstance(
        grounding_summary, list
    ):
        raise ValueError("PRISMA summary inputs must be arrays")
    scope_by_module = {
        str(row["module"]): row
        for row in screening_scope
        if isinstance(row, dict) and "module" in row
    }
    grounding_by_module = {
        str(row["vertical"]): row
        for row in grounding_summary
        if isinstance(row, dict) and "vertical" in row
    }
    expected_modules = {module for module, _, _ in MODULES}
    if set(scope_by_module) != expected_modules:
        raise ValueError("screening scope summary does not cover all modules")
    if set(grounding_by_module) != expected_modules:
        raise ValueError("grounding summary does not cover all modules")
    return {
        "identified": sum(
            int(grounding_by_module[module]["identified"])
            for module in expected_modules
        ),
        "deduped": sum(
            int(grounding_by_module[module]["deduped"])
            for module in expected_modules
        ),
        "verified": sum(
            int(grounding_by_module[module]["verified"])
            for module in expected_modules
        ),
        "rejected": sum(
            int(grounding_by_module[module]["rejected"])
            for module in expected_modules
        ),
        "deferred": sum(
            int(
                scope_by_module[module][
                    "not_advanced_due_rapid_review_cap"
                ]
            )
            for module in expected_modules
        ),
    }


def write_full_review(
    blind_root: Path,
    output: Path,
    module_outputs: dict[str, dict[str, object]],
    global_output: dict[str, object],
    claim_by_id: dict[str, dict[str, str]],
    metadata: list[dict[str, object]],
    by_module_counts: dict[str, Counter[str]],
    total_counts: Counter[str],
) -> Path:
    prisma = prisma_flow_counts(blind_root)
    extracted_occurrences = sum(
        len(result["extractions"]) for result in module_outputs.values()
    )
    lines = [
        f"# {TITLE}",
        "",
        "## Blinded Claude Secondary AI Review",
        "",
        f"> {ROLE_STATEMENT}",
        "",
        "## Executive synthesis",
        "",
        str(global_output["overall_synthesis"]),
        "",
        f"**Global null hypothesis:** {str(global_output['global_null_hypothesis']).title()}.",
        "",
        "## Methods and review ceiling",
        "",
        "- Review type: rapid systematic evidence review with narrative synthesis.",
        "- Protocol and original user prompt: supplied unaltered to Claude.",
        "- Search sources: PubMed and Europe PMC, using the engine's frozen search outputs.",
        "- Publication cutoff: 2026-07-29; search execution completed 2026-07-30.",
        "- Screening: seven modules, 140 ordered title/abstract records per module.",
        "- Claude execution: fresh nonpersistent safe-mode sessions, no tools, no web search, no Codex outputs.",
        "- Evidence basis: inspected abstracts only; full-text screening remains pending.",
        "- Formal study-level RoB and GRADE were not completed and are not claimed.",
        "- The rapid-review cap deferred records rather than excluding them.",
        "- The Reviewer-1 targeted supplemental selections were withheld from Claude to preserve blindness and are excluded from primary independence metrics.",
        "",
        "## PRISMA-style flow for the shared frozen corpus",
        "",
        "| Stage | Occurrences |",
        "|---|---:|",
        f"| Identified across modules | {prisma['identified']:,} |",
        f"| After within-module deduplication | {prisma['deduped']:,} |",
        f"| Live verified | {prisma['verified']:,} |",
        f"| Rejected/unresolved during grounding | {prisma['rejected']:,} |",
        f"| Advanced to Claude AI Reviewer 2 | {sum(total_counts.values()):,} |",
        f"| Claude include | {total_counts['include']:,} |",
        f"| Claude uncertain | {total_counts['uncertain']:,} |",
        f"| Claude exclude | {total_counts['exclude']:,} |",
        f"| Deferred by rapid cap, not screened | {prisma['deferred']:,} |",
        f"| Extracted abstract-level source occurrences | {extracted_occurrences:,} |",
        f"| Unique PubMed records in extraction | {len(metadata):,} |",
        "",
        "## Module screening summary",
        "",
        "| Module | Screened | Include | Uncertain | Exclude | Extracted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for module, _, title in MODULES:
        counts = by_module_counts[module]
        lines.append(
            f"| {title} | {sum(counts.values())} | {counts['include']} | "
            f"{counts['uncertain']} | {counts['exclude']} | "
            f"{len(module_outputs[module]['extractions'])} |"
        )
    for module, code, title in MODULES:
        lines.extend(
            (
                "",
                f"## Module {code}: {title}",
                "",
                str(module_outputs[module]["module_summary"]),
                "",
            )
        )
        for claim in (
            row for row in claim_by_id.values() if row["module"] == code
        ):
            assessment = next(
                row
                for row in module_outputs[module]["claim_assessments"]
                if row["claim_id"] == claim["claim_id"]
            )
            lines.extend(("", claim_markdown(claim, assessment), ""))
    lines.extend(("", "## Global model, null, and boundary claims", ""))
    for claim in (
        row for row in claim_by_id.values() if row["module"] == "G"
    ):
        assessment = next(
            row
            for row in global_output["claim_assessments"]
            if row["claim_id"] == claim["claim_id"]
        )
        lines.extend(("", claim_markdown(claim, assessment), ""))
    lines.extend(
        (
            "",
            "## Safety-relevant evidence-review considerations",
            "",
            *[
                f"- {item}"
                for item in global_output["safety_relevant_considerations"]
            ],
            "",
            "## Review limitations",
            "",
            *[f"- {item}" for item in global_output["review_limitations"]],
            "- Embase, PsycINFO, Scopus, Web of Science, CENTRAL, and paid full texts were not independently searched by Claude.",
            "- AI-AI agreement is not accuracy and cannot substitute for human adjudication.",
            "",
            "## Verified references",
            "",
        )
    )
    for index, record in enumerate(metadata, start=1):
        lines.append(
            f"{index}. {citation(record)} "
            f"[PubMed]({record['pubmed_url']})"
        )
    path = output / "systematic_evidence_review_full.md"
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def write_psychiatrist_brief(
    output: Path,
    module_outputs: dict[str, dict[str, object]],
    global_output: dict[str, object],
    claim_by_id: dict[str, dict[str, str]],
) -> Path:
    assessments = {
        row["claim_id"]: row
        for result in module_outputs.values()
        for row in result["claim_assessments"]
    }
    assessments.update(
        {row["claim_id"]: row for row in global_output["claim_assessments"]}
    )
    stronger = [
        claim_id
        for claim_id, row in assessments.items()
        if row["verdict"] in {"supported", "partially supported"}
        and row["certainty"] in {"high", "moderate"}
    ]
    weaker = [
        claim_id
        for claim_id, row in assessments.items()
        if row["verdict"] in {"indeterminate", "unsupported", "contradicted"}
        or row["certainty"] == "very low"
    ]
    lines = [
        f"# Psychiatrist brief: {TITLE}",
        "",
        f"> {ROLE_STATEMENT}",
        "",
        "## Established history",
        "",
        "- This literature review independently establishes no fact about the individual. Treating records, collateral history, and direct clinical assessment remain the sources for established history.",
        "",
        "## Self-reported experiences in the source protocol",
        "",
        "- Lifelong attentional, organizational, time-management, social-decoding, emotional-regulation, and coordination difficulties.",
        "- Variable performance with interest, novelty, urgency, structure, sleep, mood, substances, and medication state.",
        "- Perceived strengths in ideation, pattern recognition, and interdisciplinary synthesis, plus perceived benefit from external scaffolds.",
        "- Periods involving reduced sleep, rapid speech, irritability, salience, impulsivity, or unusual certainty require state-sensitive clinical assessment.",
        "",
        "## Literature-supported possibilities",
        "",
    ]
    for claim_id in stronger[:14]:
        lines.append(
            f"- **{claim_id}:** {assessments[claim_id]['calibrated_claim']} "
            f"({assessments[claim_id]['verdict']}; "
            f"{assessments[claim_id]['certainty']} certainty)"
        )
    lines.extend(
        (
            "",
            "## Plausible but weakly evidenced or unresolved components",
            "",
        )
    )
    for claim_id in weaker[:14]:
        lines.append(
            f"- **{claim_id}:** {assessments[claim_id]['calibrated_claim']} "
            f"({assessments[claim_id]['verdict']}; "
            f"{assessments[claim_id]['certainty']} certainty)"
        )
    lines.extend(
        (
            "",
            "## Competing explanations",
            "",
            "- Bipolar-spectrum episodes or mixed states; sleep deprivation or circadian disruption; cannabis, stimulant, nicotine, or other medication effects.",
            "- Anxiety, trauma-related processes, depression, rejection-related distress, burnout, learned interpersonal patterns, and medical or neurological contributors.",
            "- Autism-spectrum or developmental-coordination traits, multiple interacting conditions, and retrospective reconstruction rather than one unified explanation.",
            "",
            "## Diagnostic uncertainties and claim ceiling",
            "",
            f"- The global null hypothesis was **{global_output['global_null_hypothesis']}**.",
            "- Group-level evidence informs plausibility and assessment priorities; it cannot diagnose this individual or establish personal causation.",
            "- Full texts, design-specific risk-of-bias assessment, human Reviewer 2 adjudication, and treating-clinician sign-off remain pending.",
            "",
            "## Suggested discriminating questions and assessments",
            "",
            "- Developmental interview plus school, training, work, and collateral records predating mood episodes, sleep disruption, substances, and medication changes.",
            "- Structured ADHD, autism, bipolar-spectrum, anxiety, trauma, substance-use, sleep/circadian, and developmental-coordination assessments as clinically appropriate.",
            "- Prospective dated tracking of sleep, mood, substances, medication exposure, speech/activation, unusual salience or certainty, and objective functioning across settings.",
            "- Process measures for compensation: time, effort, support dependence, errors, rebound, recovery cost, and performance when structure changes.",
            "",
            "## Immediate safety-relevant considerations",
            "",
            *[
                f"- {item}"
                for item in global_output["safety_relevant_considerations"]
            ],
            "",
            "This brief is an evidence-review aid for discussion, not a diagnosis or treatment directive.",
        )
    )
    path = output / "psychiatrist_brief.md"
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def write_search_strategy(
    blind_root: Path,
    output: Path,
    by_module_counts: dict[str, Counter[str]],
) -> Path:
    lines = [
        "# Search strategy and Claude secondary-review methods",
        "",
        f"> {ROLE_STATEMENT}",
        "",
        "Claude did not rerun database searches. To make reviewer agreement interpretable, it received the exact frozen ordered title/abstract corpus produced by the evidence-synthesis engine for Codex Reviewer 1. Search execution, queries, caps, and frozen-set hashes are reproduced below.",
        "",
        "- Sources actually executed: PubMed and Europe PMC.",
        "- Publication cutoff: 2026-07-29.",
        "- Profile: rapid systematic evidence review.",
        "- Per-module rapid cap: 140 verified title/abstract records.",
        "- Deduplication: within-module identifier/title normalization in the engine; cross-module occurrences retained.",
        "- Screening: Claude Opus 5, fresh safe-mode no-tool sessions, one ordered 140-record batch per module.",
        "- Full text: not completed; all included/uncertain records remain full-text required.",
        "- Citation chaining and targeted Reviewer-1 supplements: not supplied to Claude because the stored files reveal Reviewer-1 selections; excluded from primary independence metrics.",
        "",
    ]
    for module, code, title in MODULES:
        execution = load_json(
            blind_root
            / "inputs"
            / "modules"
            / module
            / "search_execution.json"
        )
        manifest = load_json(
            blind_root
            / "inputs"
            / "modules"
            / module
            / "search_manifest.json"
        )
        counts = by_module_counts[module]
        lines.extend(
            (
                f"## Module {code}: {title}",
                "",
                f"- Search started: `{execution['started_utc']}`",
                f"- Search completed: `{execution['completed_utc']}`",
                f"- Identified: {execution['identified']}; deduped: {execution['deduped']}; verified: {execution['verified']}; rejected: {execution['rejected']}.",
                f"- Claude screening: include {counts['include']}, uncertain {counts['uncertain']}, exclude {counts['exclude']}.",
                f"- Frozen set hash: `{manifest['frozen_set_hash']}`",
                "",
            )
        )
        for source, query in execution["source_queries"].items():
            lines.extend(
                (
                    f"### {source}",
                    "",
                    "```text",
                    str(query),
                    "```",
                    "",
                )
            )
    path = output / "search_strategy.md"
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def write_exclusions(blind_root: Path, output: Path) -> Path:
    lines = [
        "# Excluded studies",
        "",
        f"> {ROLE_STATEMENT}",
        "",
        "No full-text eligibility assessment was completed. The rows below are Claude title/abstract exclusions from the frozen rapid-review sample. Records deferred by the rapid cap are not exclusions.",
        "",
    ]
    for module, code, title in MODULES:
        candidates = load_json(
            blind_root
            / "inputs"
            / "modules"
            / module
            / "screening_candidates.json"
        )
        by_position = {
            index: row for index, row in enumerate(candidates, start=1)
        }
        decisions = load_json(
            blind_root
            / "outputs"
            / "modules"
            / module
            / "screening_ai_r2_claude.json"
        )
        excluded = [
            (index, row, by_position[index])
            for index, row in enumerate(decisions, start=1)
            if row["decision"] == "exclude"
        ]
        lines.extend(
            (
                f"## Module {code}: {title}",
                "",
                f"Title/abstract exclusions: {len(excluded)}.",
                "",
                "| Occurrence | PMID | Title | Reason |",
                "|---:|---:|---|---|",
            )
        )
        for index, decision, candidate in excluded:
            title_value = str(candidate["title"]).replace("|", "\\|")
            reason = str(decision["reason"]).replace("|", "\\|")
            lines.append(
                f"| {index} | {decision['pmid']} | {title_value} | {reason} |"
            )
        lines.append("")
    path = output / "excluded_studies.md"
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def write_rejected(
    blind_root: Path,
    output: Path,
    metadata: list[dict[str, object]],
) -> Path:
    selected_flags = [
        row
        for row in metadata
        if row["retraction_or_correction_status"]
        != "No retraction/correction link found in PubMed record"
    ]
    lines = [
        "# Unverified or rejected references",
        "",
        f"> {ROLE_STATEMENT}",
        "",
        "All PMIDs used in Claude's extraction were returned by NCBI PubMed EFetch and matched the frozen candidate titles. Any source rejected during engine grounding was unavailable to Claude as evidence.",
        "",
        "## Selected-source retraction/correction flags",
        "",
    ]
    if selected_flags:
        for row in selected_flags:
            lines.append(
                f"- PMID {row['pmid']}: {row['retraction_or_correction_status']}"
            )
    else:
        lines.append("- No selected source carried a PubMed retraction/correction link.")
    lines.extend(("", "## Grounding rejections by module", ""))
    for module, code, title in MODULES:
        corpus = load_json(
            blind_root / "inputs" / "modules" / module / "corpus.json"
        )
        rejected = corpus["rejected"]
        lines.extend(
            (
                f"### Module {code}: {title}",
                "",
                f"Rejected/unresolved occurrences: {len(rejected)}.",
                "",
            )
        )
        for row in rejected:
            lines.append(
                f"- `{row.get('key', 'unidentified')}` — {row.get('reason', 'reason NR')}"
            )
        if not rejected:
            lines.append("- None.")
        lines.append("")
    path = output / "unverified_or_rejected_references.md"
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-root", type=Path, default=DEFAULT_BLIND_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-doi-resolution", action="store_true")
    args = parser.parse_args()
    blind_root = args.blind_root.resolve()
    output = args.output.resolve()
    build_started_at_utc = datetime.now(timezone.utc).isoformat()
    seal_path, scientific_seal, seal_verification = verify_scientific_seal(
        blind_root
    )
    output.mkdir(parents=True, exist_ok=True)

    module_outputs, global_output, claim_by_id = load_reviewer_outputs(
        blind_root
    )
    by_module_counts, total_counts = screening_counts(blind_root)
    metadata = fetch_metadata(
        blind_root,
        output,
        module_outputs,
        resolve_dois=not args.skip_doi_resolution,
    )
    metadata_fetched_at_utc = datetime.now(timezone.utc).isoformat()
    paths = [
        output / "selected_pubmed_efetch.xml",
        output / "selected_pubmed_metadata.json",
        write_verified_references(output, metadata),
        write_matrix(output, module_outputs, global_output, metadata),
        write_full_review(
            blind_root,
            output,
            module_outputs,
            global_output,
            claim_by_id,
            metadata,
            by_module_counts,
            total_counts,
        ),
        write_psychiatrist_brief(
            output,
            module_outputs,
            global_output,
            claim_by_id,
        ),
        write_search_strategy(blind_root, output, by_module_counts),
        write_exclusions(blind_root, output),
        write_rejected(blind_root, output, metadata),
    ]
    provenance = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": {
            "secondary_ai_reviewer_claude": "complete",
            "ai_ai_reconciliation": "not started",
            "human_reviewer_2": "pending",
            "full_text_screening": "pending",
            "formal_risk_of_bias": "pending",
            "clinical_sign_off": "pending",
            "official_engine_export": "withheld",
        },
        "role_statement": ROLE_STATEMENT,
        "blind_input_manifest_sha256": sha256(
            blind_root / "provenance" / "blind_input_manifest.json"
        ),
        "scientific_seal_sha256": sha256(seal_path),
        "source_integrity": {
            "scientific_seal": hash_record(seal_path),
            "scientific_seal_created_at_utc": scientific_seal.get(
                "sealed_at_utc"
            ),
            "blind_input_manifest": hash_record(
                blind_root / "provenance" / "blind_input_manifest.json"
            ),
            "builder_script": hash_record(Path(__file__).resolve()),
            **seal_verification,
        },
        "metadata_verification": {
            "build_started_at_utc": build_started_at_utc,
            "pubmed_efetch_completed_at_utc": metadata_fetched_at_utc,
            "source": "NCBI PubMed EFetch",
            "doi_resolution_requested": not args.skip_doi_resolution,
        },
        "screening": dict(total_counts),
        "extracted_occurrences": sum(
            len(result["extractions"]) for result in module_outputs.values()
        ),
        "unique_verified_pmids": len(metadata),
        "claim_assessments": 66,
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in paths
        },
    }
    provenance_path = output / "secondary_ai_reviewer_provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "screened": sum(total_counts.values()),
                "extracted_occurrences": provenance["extracted_occurrences"],
                "unique_verified_pmids": len(metadata),
                "claim_assessments": 66,
                "artifacts": len(paths) + 1,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
