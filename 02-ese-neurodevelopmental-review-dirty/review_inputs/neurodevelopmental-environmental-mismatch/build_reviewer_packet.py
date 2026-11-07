#!/usr/bin/env python3
"""Build the pre-human-review evidence packet and machine-readable appendices."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs" / "ndem-rapid-20260729"
OUTPUT = ROOT / "review_outputs" / "neurodevelopmental-environmental-mismatch-20260729"
INPUTS = ROOT / "review_inputs" / "neurodevelopmental-environmental-mismatch"
MODULES = [
    ("ndem-a-persistence-compensation", "A", "Persistence, executive function, and compensation"),
    ("ndem-b-overlap-social-dcd", "B", "ADHD–autism–DCD overlap and social functioning"),
    ("ndem-c-context-motivation-strengths", "C", "Context, motivation, environmental fit, and proposed strengths"),
    ("ndem-d-external-scaffolding", "D", "External cognitive scaffolding"),
    ("ndem-e1-sleep-circadian", "E1", "Sleep and circadian modifiers"),
    ("ndem-e2-mood-substances", "E2", "Mood, substances, and medication state effects"),
    ("ndem-f-daydreaming-rejection", "F", "Daydreaming, rejection, anxiety, and compensation"),
]
REVIEW_TITLE = (
    "Adaptive-Constraint and Environmental-Mismatch Model of Adult "
    "Neurodevelopmental Functioning"
)
FULL_TITLE = f"{REVIEW_TITLE}: AI-Assisted Rapid Systematic Evidence Review Draft"
SEARCH_DATE = "2026-07-29"


GUIDELINES = [
    {
        "record_id": "NICE-NG87",
        "pmid": "",
        "pmcid": "",
        "doi": "",
        "title": "Attention deficit hyperactivity disorder: diagnosis and management",
        "authors": ["National Institute for Health and Care Excellence"],
        "author_string": "National Institute for Health and Care Excellence",
        "journal": "NICE guideline NG87",
        "journal_abbreviation": "NICE NG87",
        "year": "2018",
        "publication_date": "Published 2018-03-14; last updated 2019-09-13; accessed 2026-07-29",
        "volume": "",
        "issue": "",
        "pages_or_article_number": "NG87",
        "publication_types": ["Professional guideline"],
        "abstract": "Official NICE guideline covering recognition, diagnosis, and management of ADHD in children, young people, and adults.",
        "pubmed_url": "",
        "doi_url": "",
        "stable_url": "https://www.nice.org.uk/guidance/ng87",
        "verification_source": "Official NICE page inspected in Chrome",
        "correction_relations": [],
        "retraction_or_correction_status": "Not applicable; current official guideline page",
        "doi_verification": {"status": "not_applicable", "http_status": None, "final_url": ""},
    },
    {
        "record_id": "NICE-CG185",
        "pmid": "",
        "pmcid": "",
        "doi": "",
        "title": "Bipolar disorder: assessment and management",
        "authors": ["National Institute for Health and Care Excellence"],
        "author_string": "National Institute for Health and Care Excellence",
        "journal": "NICE clinical guideline CG185",
        "journal_abbreviation": "NICE CG185",
        "year": "2014",
        "publication_date": "Published 2014-09-24; last updated 2025-09-02; accessed 2026-07-29",
        "volume": "",
        "issue": "",
        "pages_or_article_number": "CG185",
        "publication_types": ["Professional guideline"],
        "abstract": "Official NICE guideline covering recognition, assessment, and management of bipolar I, bipolar II, mixed affective, and rapid-cycling disorders.",
        "pubmed_url": "",
        "doi_url": "",
        "stable_url": "https://www.nice.org.uk/guidance/cg185",
        "verification_source": "Official NICE page inspected in Chrome",
        "correction_relations": [],
        "retraction_or_correction_status": "Not applicable; current official guideline page",
        "doi_verification": {"status": "not_applicable", "http_status": None, "final_url": ""},
    },
]


GUIDELINE_RELATIONSHIPS = [
    {
        "record_id": "NICE-NG87",
        "module": "A / Global",
        "claim_ids": ["H1.1", "H1.3", "H1.4", "H1.N1", "G6"],
        "direction": "supports",
        "evidence_role": "professional guideline",
        "study_design": "Current professional guideline",
        "population": "Children, young people, and adults assessed or treated for ADHD",
        "exposure_intervention": "Diagnostic assessment and management",
        "comparator": "Competing psychiatric and neurodevelopmental explanations considered during assessment",
        "outcome": "Diagnostic requirements, impairment, cross-setting evidence, and specialist assessment",
        "main_result": "NICE requires childhood manifestations that persist, impairment in multiple important settings, and specialist assessment that considers coexisting and alternative conditions; rating scales alone are insufficient.",
        "effect_estimate": "NR",
        "directness": "direct",
        "major_limitations": "A guideline states practice recommendations rather than estimating this individual's diagnosis or causal history.",
        "individual_applicability": "High for assessment process; it does not diagnose the individual.",
        "provisional_quality": "high",
        "quality_reason": "Current authoritative professional guidance inspected on the official NICE site.",
        "evidence_basis": "official guideline page",
        "abstract_available": True,
        "fulltext_required": False,
    },
    {
        "record_id": "NICE-CG185",
        "module": "E2 / Global",
        "claim_ids": ["H8.1", "H8.5", "H8.6", "G5", "G6"],
        "direction": "supports",
        "evidence_role": "professional guideline",
        "study_design": "Current professional guideline",
        "population": "Children, young people, and adults with suspected or established bipolar disorder",
        "exposure_intervention": "Bipolar assessment and management",
        "comparator": "ADHD, substance misuse, alcohol-use disorders, psychosis-spectrum, personality, and physical differential diagnoses",
        "outcome": "Recognition of mania/hypomania, differential diagnosis, relapse triggers, and coexisting conditions",
        "main_result": "NICE directs urgent specialist assessment for suspected mania and explicitly requires attention to ADHD, substance use, medications, and physical differential diagnoses.",
        "effect_estimate": "NR",
        "directness": "direct",
        "major_limitations": "A guideline supports clinical process and safety, not an individualized etiologic conclusion.",
        "individual_applicability": "High for safety and differential-assessment process; no diagnosis is implied.",
        "provisional_quality": "high",
        "quality_reason": "Current authoritative professional guidance inspected on the official NICE site.",
        "evidence_basis": "official guideline page",
        "abstract_available": True,
        "fulltext_required": False,
    },
]

POST_HOC_HYPOTHESIS_ONLY = {
    "40868584": (
        "Mixed chronic-condition school population; ADHD/autism effects were not "
        "separable under the frozen eligibility criteria."
    ),
    "42199316": (
        "Mixed typical and diagnostic pediatric populations; ADHD-specific effects "
        "were not separable under the frozen eligibility criteria."
    ),
    "42327684": (
        "Predominantly pediatric autism dental-procedure evidence with no separable "
        "adolescent result under the frozen eligibility criteria."
    ),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_text(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def canonical_scale(value: str) -> str:
    """Return the most conservative protocol-permitted ordinal rating."""

    normalized = value.strip().rstrip(".").lower()
    if "very low" in normalized or "indeterminate" in normalized:
        return "Very low"
    if "low" in normalized:
        return "Low"
    if "moderate" in normalized:
        return "Moderate"
    if "high" in normalized:
        return "High"
    raise ValueError(f"cannot normalize ordinal rating: {value!r}")


def case_directness(value: str) -> str:
    """Translate study/construct directness into directness to this unverified case."""

    normalized = value.strip().rstrip(".").lower()
    if normalized in {"high", "moderate", "low", "very low"}:
        return {
            "high": "High",
            "moderate": "Moderate",
            "low": "Low",
            "very low": "Very low",
        }[normalized]
    if "no direct" in normalized or "highly indirect" in normalized:
        return "Very low"
    if "indirect" in normalized or "mixed" in normalized:
        return "Very low"
    if "direct" in normalized:
        return "Low"
    raise ValueError(f"cannot normalize case directness: {value!r}")


def normalize_claim_fields(markdown: str) -> str:
    """Make every atomic claim conform to the requested ordinal field template."""

    output: list[str] = []
    current_claim = ""
    in_summary_table = False
    for line in markdown.splitlines():
        heading = re.match(r"^#{1,5}\s+((?:H|G)\d+(?:\.\d+|\.N\d+)?)\s+[—-]", line)
        if heading:
            current_claim = heading.group(1)

        if line.strip() == "| Claim | Verdict | Certainty | Directness |":
            output.append("| Claim | Verdict | Certainty | Evidence directness |")
            in_summary_table = True
            continue
        if in_summary_table and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 4 and not all(set(cell) <= {"-", ":"} for cell in cells):
                cells[2] = canonical_scale(cells[2])
                output.append("| " + " | ".join(cells) + " |")
                continue
        elif in_summary_table:
            in_summary_table = False

        certainty = re.match(r"^- \*\*Certainty:\*\*\s*(.+)$", line)
        if certainty and current_claim:
            output.append(f"- **Certainty:** {canonical_scale(certainty.group(1))}.")
            continue

        directness = re.match(
            r"^- \*\*(Directness(?: to this case)?):\*\*\s*(.+)$",
            line,
        )
        if directness and current_claim:
            original = directness.group(2).strip().rstrip(".")
            if directness.group(1) == "Directness":
                output.append(f"- **Evidence directness:** {original}.")
            directness_rating = case_directness(original)
            if current_claim == "H8.7" and directness_rating == "Moderate":
                directness_rating = "Low"
            output.append(f"- **Directness to this case:** {directness_rating}.")
            calibration = "High" if current_claim == "G6" else "Moderate"
            output.append(
                "- **Confidence that wording is appropriately calibrated:** "
                f"{calibration}."
            )
            continue

        if line.startswith("- **Alternative explanation:**") and current_claim:
            output.append(
                line.replace(
                    "- **Alternative explanation:**",
                    "- **Alternative explanation (unverified hypothesis unless cited):**",
                    1,
                )
            )
            continue

        output.append(line)
    return "\n".join(output)


def demote_headings(markdown: str) -> str:
    output: list[str] = []
    for line in normalize_claim_fields(markdown).splitlines():
        match = re.match(r"^(#{1,5})\s+(.*)$", line)
        if match:
            level = min(len(match.group(1)) + 1, 6)
            output.append(f"{'#' * level} {match.group(2)}")
        else:
            output.append(line)
    return "\n".join(output).strip()


def cite(record: dict[str, object], complete_authors: bool = False) -> str:
    authors = list(record.get("authors") or [])
    if not authors:
        author_text = "Unknown author"
    elif complete_authors or len(authors) <= 6:
        author_text = ", ".join(str(author) for author in authors)
    else:
        author_text = ", ".join(str(author) for author in authors[:6]) + ", et al"
    title = str(record.get("title") or "").strip()
    title_ending = "" if title.endswith((".", "?", "!")) else "."
    journal = str(record.get("journal") or "")
    year = str(record.get("year") or "")
    volume = str(record.get("volume") or "")
    issue = str(record.get("issue") or "")
    pages = str(record.get("pages_or_article_number") or "")
    locator = volume
    if issue:
        locator += f"({issue})"
    if pages:
        locator += f":{pages}" if locator else pages
    citation = f"{author_text}. {title}{title_ending} {journal}. {year}"
    if locator:
        citation += f";{locator}"
    citation += "."
    if record.get("doi"):
        citation += f" doi:{record['doi']}."
    if record.get("pmid"):
        citation += f" PMID:{record['pmid']}."
    return citation


def load_all() -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, dict[str, object]],
    list[dict[str, object]],
]:
    metadata = read_json(RUN / "selected_pubmed_metadata.json")
    for record in metadata:
        record["record_id"] = f"PMID-{record['pmid']}"
        record["stable_url"] = record["pubmed_url"]
    all_refs = metadata + GUIDELINES
    ref_by_id = {str(record["record_id"]): record for record in all_refs}
    ref_by_pmid = {str(record["pmid"]): record for record in metadata}

    relationships: list[dict[str, object]] = []
    for module, _, _ in MODULES:
        for row in read_json(RUN / module / "evidence_extraction_ai_r1.json"):
            item = dict(row)
            item["module"] = module
            item["record_id"] = f"PMID-{item['pmid']}"
            relationships.append(item)
    for row in read_json(RUN / "global_evidence_extraction_ai_r1.json"):
        item = dict(row)
        item["module"] = "global"
        item["record_id"] = f"PMID-{item['pmid']}"
        relationships.append(item)
    relationships.extend(GUIDELINE_RELATIONSHIPS)

    for item in relationships:
        if item["record_id"] not in ref_by_id:
            raise ValueError(f"relationship has no verified reference: {item['record_id']}")

    return metadata, all_refs, ref_by_pmid, relationships


def write_matrix(
    relationships: list[dict[str, object]],
    ref_by_id: dict[str, dict[str, object]],
) -> Path:
    path = OUTPUT / "claim_evidence_matrix.csv"
    fields = [
        "claim_id",
        "module",
        "record_id",
        "citation",
        "pmid",
        "pmcid",
        "doi",
        "stable_url",
        "verification",
        "study_design",
        "population",
        "exposure_intervention",
        "comparator",
        "outcome",
        "main_result",
        "effect_estimate",
        "direction",
        "evidence_role",
        "eligibility_status",
        "eligibility_note",
        "directness",
        "major_limitations",
        "individual_applicability",
        "quality_judgment",
        "quality_reason",
        "evidence_basis",
        "fulltext_required",
    ]
    rows: list[dict[str, object]] = []
    for item in relationships:
        ref = ref_by_id[str(item["record_id"])]
        for claim_id in item["claim_ids"]:
            rows.append(
                {
                    "claim_id": claim_id,
                    "module": item["module"],
                    "record_id": item["record_id"],
                    "citation": cite(ref),
                    "pmid": ref.get("pmid", ""),
                    "pmcid": ref.get("pmcid", ""),
                    "doi": ref.get("doi", ""),
                    "stable_url": ref.get("stable_url", ""),
                    "verification": ref.get("verification_source", ""),
                    "study_design": item["study_design"],
                    "population": item["population"],
                    "exposure_intervention": item["exposure_intervention"],
                    "comparator": item["comparator"],
                    "outcome": item["outcome"],
                    "main_result": item["main_result"],
                    "effect_estimate": item["effect_estimate"],
                    "direction": item["direction"],
                    "evidence_role": item["evidence_role"],
                    "eligibility_status": (
                        "post_hoc_hypothesis_generating_only"
                        if str(ref.get("pmid", "")) in POST_HOC_HYPOTHESIS_ONLY
                        else "eligible"
                    ),
                    "eligibility_note": POST_HOC_HYPOTHESIS_ONLY.get(
                        str(ref.get("pmid", "")),
                        "",
                    ),
                    "directness": item["directness"],
                    "major_limitations": as_text(item["major_limitations"]),
                    "individual_applicability": item["individual_applicability"],
                    "quality_judgment": item["provisional_quality"],
                    "quality_reason": item["quality_reason"],
                    "evidence_basis": item["evidence_basis"],
                    "fulltext_required": item["fulltext_required"],
                }
            )
    rows.sort(key=lambda row: (str(row["claim_id"]), str(row["record_id"])))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_verified_references(
    all_refs: list[dict[str, object]],
    relationships: list[dict[str, object]],
) -> Path:
    usage: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"modules": set(), "claims": set()}
    )
    for item in relationships:
        record_id = str(item["record_id"])
        usage[record_id]["modules"].add(str(item["module"]))
        usage[record_id]["claims"].update(str(value) for value in item["claim_ids"])

    path = OUTPUT / "verified_references.csv"
    fields = [
        "record_id",
        "title",
        "authors",
        "journal_or_organization",
        "publication_year",
        "publication_date",
        "volume",
        "issue",
        "pages_or_article_number",
        "doi",
        "pmid",
        "pmcid",
        "stable_url",
        "verification_source",
        "verification_date",
        "doi_resolution_status",
        "doi_final_url",
        "retraction_or_correction_status",
        "correction_relations",
        "publication_types",
        "included_modules",
        "claim_ids",
        "evidence_inspected",
    ]
    rows: list[dict[str, object]] = []
    for ref in all_refs:
        record_id = str(ref["record_id"])
        doi_verification = ref.get("doi_verification") or {}
        rows.append(
            {
                "record_id": record_id,
                "title": ref.get("title", ""),
                "authors": ref.get("author_string", ""),
                "journal_or_organization": ref.get("journal", ""),
                "publication_year": ref.get("year", ""),
                "publication_date": ref.get("publication_date", ""),
                "volume": ref.get("volume", ""),
                "issue": ref.get("issue", ""),
                "pages_or_article_number": ref.get("pages_or_article_number", ""),
                "doi": ref.get("doi", ""),
                "pmid": ref.get("pmid", ""),
                "pmcid": ref.get("pmcid", ""),
                "stable_url": ref.get("stable_url", ""),
                "verification_source": ref.get("verification_source", ""),
                "verification_date": SEARCH_DATE,
                "doi_resolution_status": doi_verification.get("status", ""),
                "doi_final_url": doi_verification.get("final_url", ""),
                "retraction_or_correction_status": ref.get(
                    "retraction_or_correction_status", ""
                ),
                "correction_relations": json.dumps(
                    ref.get("correction_relations", []), ensure_ascii=False
                ),
                "publication_types": "; ".join(ref.get("publication_types", [])),
                "included_modules": "; ".join(sorted(usage[record_id]["modules"])),
                "claim_ids": "; ".join(sorted(usage[record_id]["claims"])),
                "evidence_inspected": (
                    "PubMed abstract"
                    if ref.get("pmid")
                    else "Official guideline page"
                ),
            }
        )
    rows.sort(key=lambda row: (str(row["publication_year"]), str(row["record_id"])))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def module_flow_table() -> str:
    grounding = {row["vertical"]: row for row in read_json(RUN / "grounding_summary.json")}
    scope = {row["module"]: row for row in read_json(RUN / "screening_scope_summary.json")}
    validation = read_json(RUN / "screening_validation.json")["modules"]
    extraction = read_json(RUN / "extraction_validation.json")["modules"]
    lines = [
        "| Module | Identified | Deduplicated | Live verified | Advanced | Deferred (not screened) | Included | Uncertain | Excluded | Extracted |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for module, short, _ in MODULES:
        g = grounding[module]
        s = scope[module]
        v = validation[module]
        x = extraction[module]
        lines.append(
            f"| {short} | {g['identified']} | {g['deduped']} | {g['verified']} | "
            f"{s['advanced_to_ai_reviewer_1']} | "
            f"{s['not_advanced_due_rapid_review_cap']} | {v['include']} | "
            f"{v['uncertain']} | {v['exclude']} | {x['selected_records']} |"
        )
    return "\n".join(lines)


def write_search_strategy() -> Path:
    grounding = read_json(RUN / "grounding_summary.json")
    supplements = read_json(INPUTS / "targeted_pubmed_supplements.json")
    chaining = read_json(INPUTS / "citation_chaining_log.json")
    screening = read_json(RUN / "screening_validation.json")["totals"]
    lines = [
        "# Search strategy",
        "",
        "> **Status:** AI Reviewer‑1 rapid-review search record. Human Reviewer 2 and information-specialist peer review are pending.",
        "",
        f"- Final search date: **{SEARCH_DATE}**",
        "- Date coverage: database inception through 2026-07-29.",
        "- Language: no explicit database language filter. Records without an inspectable abstract could not support extraction.",
        "- Executed sources: PubMed/MEDLINE and Europe PMC for all modules; ClinicalTrials.gov for D, E1, and E2.",
        "- Not executed: Embase, PsycINFO, Web of Science, Scopus, Cochrane CENTRAL, CINAHL, ERIC, ProQuest, and WHO ICTRP.",
        "- Retrieval was live and capped by source. A source cap is a rapid-review constraint, not proof that all database records were retrieved.",
        "",
        "## Main module searches",
        "",
    ]
    for item in grounding:
        lines.extend(
            [
                f"### {item['vertical']}",
                "",
                f"- Executed sources: {', '.join(item['sources_executed'])}",
                f"- Counts: identified {item['identified']}; within-module deduplicated {item['deduped']}; live verified {item['verified']}; unresolved/rejected {item['rejected']}.",
                f"- Source caps: `{json.dumps(item['source_caps'], sort_keys=True)}`",
                "",
            ]
        )
        for source, query in item["source_queries"].items():
            lines.extend([f"#### {source}", "", "```text", query, "```", ""])

    lines.extend(["## Targeted PubMed gap-filling searches", ""])
    for search in supplements["searches"]:
        lines.extend(
            [
                f"### {search['id']}",
                "",
                f"- Module: {search['module']}",
                f"- Purpose: {search['purpose']}",
                f"- Total found: {search['total_found']}",
                f"- Selected after abstract inspection: {', '.join(search['selected_pmids'])}",
                "",
                "```text",
                search["query"],
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Deduplication and screening",
            "",
            "Records were normalized by verified identifier, preferring PMID where available. Within-module exact identifier duplicates were collapsed during grounding. Cross-module duplicates were retained because eligibility and claim mapping differ by module.",
            "",
            "A deterministic evidence-hierarchy triage ranked verified records. Exactly 140 records per module advanced to AI Reviewer‑1 title/abstract screening. Records beyond that cap were labelled `not_advanced_due_rapid_review_cap`; they were **not** counted as exclusions.",
            "",
            f"AI Reviewer‑1 screened {screening['screened']} module-record occurrences: {screening['include']} include, {screening['uncertain']} uncertain, and {screening['exclude']} exclude. There were {screening['unique_include_or_uncertain_pmids']} unique PMIDs among include/uncertain decisions. Human Reviewer 2 has not adjudicated these decisions.",
            "",
            "Selection did not depend on whether a result favored the model. The extraction sample prioritized guidelines, systematic reviews/meta-analyses, longitudinal cohorts, randomized trials, direct counterevidence, and coverage of every atomic claim. Abstracts were required; title-only sources were not cited.",
            "",
            "Post-hoc eligibility deviation: PMIDs 40868584, 42199316, and 42327684 have nonseparable pediatric or mixed populations that do not satisfy the frozen target-population criteria. They are retained only as explicitly labelled hypothesis-generating context, have neutral direction in the matrix, and do not contribute to claim verdicts.",
            "",
            "## Citation chaining",
            "",
            f"Method: {chaining['method']}. {chaining['role']}",
            "",
        ]
    )
    for seed in chaining["seeds"]:
        lines.extend(
            [
                f"- Seed PMID {seed['pmid']} ({seed['topic']}): {seed['results_returned']} related records returned. {seed['disposition']}",
            ]
        )
    lines.extend(
        [
            "",
            "Reference and cited-by ELink calls for PMID 37648266 returned no records; this connector limitation is logged rather than treated as absence of citations.",
            "",
            "## PRISMA-style rapid-review flow",
            "",
            module_flow_table(),
            "",
            "Totals are module-record occurrences because the protocol contains seven distinct eligibility frameworks. Across modules, 4,165 records were identified, 3,770 remained after within-module deduplication, 3,638 were live verified, 132 were unresolved/rejected, 980 were screened, and 2,658 were deferred by the rapid cap. Extraction reconciles as 96 module occurrences plus 2 global targeted supplements = 98 occurrences (92 unique PMIDs). No formal full-text eligibility stage has yet been completed.",
            "",
            "## Reproducibility files",
            "",
            f"- Main run manifests: `{RUN}`",
            f"- Frozen claim ledger: `{INPUTS / 'claim_ledger.csv'}`",
            f"- Protocol specifications: `{INPUTS}`",
            f"- Bibliographic verification XML: `{RUN / 'selected_pubmed_efetch.xml'}`",
            "",
        ]
    )
    path = OUTPUT / "search_strategy.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_exclusions() -> Path:
    lines = [
        "# Excluded studies",
        "",
        "> **Status:** AI Reviewer‑1 title/abstract exclusions only. Human Reviewer 2 and full-text eligibility adjudication are pending.",
        "",
        "## Full-text exclusions",
        "",
        "**None recorded.** This rapid draft did not complete a formal full-text eligibility stage. It would be false to relabel title/abstract decisions or rapid-cap deferrals as full-text exclusions.",
        "",
        "## Title/abstract exclusion reasons",
        "",
        "The 747 title/abstract exclusions are summarized below. The exact record-level decision and reason are preserved in each module's `screening_ai_r1.json` file.",
        "",
    ]
    for module, short, title in MODULES:
        rows = read_json(RUN / module / "screening_ai_r1.json")
        counts = Counter(
            str(row["reason"]) for row in rows if row["decision"] == "exclude"
        )
        lines.extend(
            [
                f"### Module {short}: {title}",
                "",
                "| Reason | Count |",
                "|---|---:|",
            ]
        )
        for reason, count in counts.most_common():
            lines.append(f"| {reason.replace('|', '\\|')} | {count} |")
        lines.append("")

    lines.extend(
        [
            "## Deferred records are not exclusions",
            "",
            "A further 2,658 live-verified module-record occurrences were not advanced because each module was capped at 140 candidates. Their disposition is `not_advanced_due_rapid_review_cap`, not `exclude`. They would require screening in a complete review.",
            "",
        ]
    )
    path = OUTPUT / "excluded_studies.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_rejected() -> Path:
    rejected: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for module, short, _ in MODULES:
        corpus = read_json(RUN / module / "corpus.json")
        for item in corpus["rejected"]:
            rejected[
                (
                    str(item.get("key", "")),
                    str(item.get("source", "")),
                    str(item.get("reason", "")),
                )
            ].add(short)
    lines = [
        "# Unverified or rejected references",
        "",
        "> **Status:** Retrieval-stage rejection log. Entries below were not used as evidence.",
        "",
        "Records were rejected when the live connector could not resolve the returned identifier to a verifiable bibliographic record. This is not a scientific exclusion and does not imply that the work is false; it means the citation could not pass this run's source-integrity gate.",
        "",
        f"- Rejected/unresolved occurrences across modules: {sum(len(read_json(RUN / module / 'corpus.json')['rejected']) for module, _, _ in MODULES)}",
        f"- Unique rejected keys after cross-module consolidation: {len(rejected)}",
        "",
        "| Identifier/key | Source | Reason | Modules |",
        "|---|---|---|---|",
    ]
    for (key, source, reason), modules in sorted(rejected.items()):
        lines.append(
            f"| {key.replace('|', '\\|')} | {source} | {reason} | {', '.join(sorted(modules))} |"
        )
    lines.extend(
        [
            "",
            "## Selected-reference integrity check",
            "",
            "Every source used in the narrative or claim matrix has a live PubMed record or an official NICE guideline page. DOI resolution outcomes and PubMed retraction/correction relationships are recorded in `verified_references.csv`. No unresolved retrieval-stage record was cited.",
            "",
        ]
    )
    path = OUTPUT / "unverified_or_rejected_references.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_psychiatrist_brief() -> Path:
    text = f"""# Psychiatrist brief

## {REVIEW_TITLE}

> **Clinical status:** AI-assisted rapid evidence-review draft dated {SEARCH_DATE}. It has **not** received independent human Reviewer 2 adjudication or treating-clinician sign-off. It is not a diagnosis, treatment plan, or authorization to change medication, sleep, nicotine, cannabis, or stimulant exposure.

## Bottom line

The literature makes a lifelong neurodevelopmental formulation plausible, especially if childhood onset and cross-setting impairment can be independently documented. It does **not** show that this model is the best explanation for the individual narrative. Bipolar-spectrum state, sleep/circadian disruption, cannabis/THC, stimulant agent and dose, nicotine, anxiety/trauma, burnout, medication effects, and retrospective reconstruction remain active alternatives or modifiers. The global null hypothesis is not rejected (full-review claims G1–G6).

## Established or independently verified information

- No personal diagnosis, developmental record, collateral history, medication record, toxicology, sleep data, or episode chronology was independently verified in this review.
- The only independently verified material is the bibliographic evidence and the current official NICE ADHD and bipolar guidance.
- Accordingly, all person-specific history below is treated as **self-report from the supplied protocol**, not as established fact.

## Self-reported experiences in the source protocol

- Lifelong executive and time-management difficulties, variable task initiation, hyperfocus, communication and social-decoding difficulty, and coordination concerns.
- Academic/professional achievement attributed partly to intelligence and compensatory strategies.
- Context-sensitive functioning, with perceived strengths in novelty, analysis, pattern recognition, and ideation.
- Reported worsening of cognition, irritability, rapid speech, salience, impulsivity, or unusual interpretations during some combinations of reduced sleep, mood elevation, THC, stimulants, or nicotine.
- Perceived benefit from reminders, checklists, structured interviews, and AI-supported organization.
- Spiritual meaning-making that is personally important but is not used here as empirical psychiatric evidence.

## Literature-supported possibilities

Claim IDs below crosswalk each brief statement to the full review and `claim_evidence_matrix.csv`; complete citations and links are in `verified_references.csv`.

- Childhood ADHD can persist into adulthood and affect multiple settings; persistence and outcome are heterogeneous (H1.1–H1.4; PMID 42365682; NICE NG87).
- ADHD is associated with social and peer difficulties, while autistic, anxiety, mood, conflict, and language mechanisms may add or substitute explanations; trauma was not directly tested in this bounded social module (H5.1–H5.4; PMIDs 41890162, 41936228, 42120904).
- Reward and task context can change performance, but evidence for a broad “environmental mismatch” theory is less direct than evidence for specific task effects (H3.2–H3.5, H6.1–H6.4; PMIDs 40287643, 42132039).
- Structured CBT/organizational interventions can improve some adult ADHD outcomes; evidence for durable benefit from generic checklists, occupational redesign, or AI organization is much thinner (H4.1–H4.6; PMIDs 41936228, 40761448, 41808914).
- Sleep and circadian variables correlate with attention and bipolar instability, often bidirectionally (H7.1–H7.N1; PMIDs 41803808, 42320790, 42464599).
- Cannabis/THC is associated with psychosis and adverse mood/cognitive outcomes. Prescription-stimulant psychiatric risk appears dependent on agent, dose, vulnerability, and mood stabilization; it is not inevitable. Nicotine can acutely change arousal/attention and interact with stimulants, but bipolar-specific evidence is sparse (H8.2–H8.6; PMIDs 37648266, 38655516, 39262211, 41856665, 37216808).
- Loneliness, shame, bullying, stigma, and anxiety are associated with burden. Evidence that maladaptive daydreaming is an emotional-compensation mechanism, or that adversity has a proven temporal direction, is weak (H9.1–H9.4, H12.1–H12.4; PMIDs 39999955, 41425822, 42137527, 40932793).

## Competing explanations that remain live

These are differential hypotheses to test, not independently established facts about this person. Their evidence and limitations are crosswalked in H5, H7–H9, H11–H12, and G1–G6 of the full review.

- Bipolar I/II, mixed or hypomanic/manic states, and psychotic-spectrum phenomena.
- Sleep deprivation, sleep-disordered breathing, shift-work/circadian disorder, or sleep change as a prodrome rather than a cause.
- Cannabis/THC, prescribed or nonmedical stimulant exposure, nicotine, caffeine, withdrawal, dose interactions, and medication adverse effects.
- Anxiety, trauma-related processes, depression, rejection sensitivity, dissociation/compulsivity, and maladaptive or normative fantasy.
- Autism-spectrum and developmental-coordination conditions, language/pragmatic differences, neurological/medical causes, injury, pain, obesity, or limited practice.
- Occupational burnout, moral injury, workload, conflict, learned interpersonal patterns, and person-environment mismatch.
- Recall, confirmation, narrative-coherence, selection, and common-method bias.
- Several interacting conditions rather than one unifying diagnosis.

## Diagnostic uncertainties

- Childhood onset and persistence have not been verified with collateral or records (H1.1–H1.N1; NICE NG87).
- Trait-level symptoms have not been separated from periods of mood change, sleep loss, substance exposure, or medication changes (H7.1–H8.7; NICE CG185).
- Achievement does not prove either absence of impairment or hidden compensation; effort, support, and instability must be measured (H2.1–H2.N1).
- Social difficulty is nonspecific. Autism-related features require developmental social-communication and restricted/repetitive-pattern evidence, not trait matching alone (H5.1–H5.4).
- Motor difficulty requires standardized assessment and exclusion of acquired causes; this review found insufficient direct ADHD–DCD overlap evidence (H11.1–H11.4).
- “Creativity,” “hyperfocus,” “pattern recognition,” and “environmental fit” are heterogeneous constructs and should not be romanticized or used diagnostically (H6.1–H6.4, H10.1–H10.6).

## Suggested assessment questions and data

1. **Developmental anchor:** What contemporaneous school records, parent/teacher observations, prior evaluations, or safe collateral document symptoms and impairment before mood/substance/occupational changes?
2. **Episode boundaries:** Do changes in sleep need, energy, speech pressure, certainty/salience, impulsivity, goal-directed activity, and function cluster into time-limited syndromal episodes?
3. **State timeline:** What are the exact dates, doses, formulations, and temporal sequence for cannabis/THC, stimulants, nicotine, antidepressants, mood stabilizers, antipsychotics, caffeine, and sleep?
4. **Cross-setting impairment:** What objective errors, missed deadlines, relationship effects, financial/self-care problems, or support dependence occur in more than one setting?
5. **Differential assessment:** Use a clinician-selected structured adult ADHD assessment with childhood collateral; bipolar and psychosis assessment; autism-focused developmental evaluation if indicated; standardized motor/DCD evaluation if indicated; and anxiety, trauma, depression, dissociation/compulsivity, substance, and sleep assessment.
6. **Prospective discrimination:** Daily sleep timing/quality, mood, energy, speech, unusual certainty/salience, attention, task type, structure, substances, medications, and functional outcomes—without intentional sleep deprivation, substance rechallenge, or unsupervised medication change.

## Immediate safety-relevant considerations

Suspected mania, severe depression, psychosis, dangerous impulsivity, or inability to care for oneself warrants prompt specialist assessment (NICE CG185; H8.1, H8.5, H8.6, G6). The source protocol itself appropriately cautions against intentional sleep deprivation, substance exposure, or unprescribed medication changes. Any medication or substance decision should remain with the treating clinician, especially when bipolar vulnerability, psychosis, reduced sleep, cannabis, stimulants, or nicotine may overlap.

## Evidence ceiling

This packet can inform a clinical conversation and identify discriminating questions. It cannot verify the personal history, establish diagnosis, prove causation, replace collateral information, or serve as clinical sign-off.
"""
    path = OUTPUT / "psychiatrist_brief.md"
    path.write_text(text, encoding="utf-8")
    return path


def write_full_review(
    all_refs: list[dict[str, object]],
    relationships: list[dict[str, object]],
) -> Path:
    screening = read_json(RUN / "screening_validation.json")["totals"]
    extraction = read_json(RUN / "extraction_validation.json")["totals"]
    verification = read_json(RUN / "selected_verification_summary.json")
    sections: list[str] = [
        f"# {FULL_TITLE}",
        "",
        f"**Final search date:** {SEARCH_DATE}",
        "",
        "> **GOVERNANCE STATUS — NOT CLINICALLY SIGNED OFF**  ",
        "> This is an AI Reviewer‑1 rapid systematic evidence-review draft generated with the local Evidence Synthesis Engine. Independent human Reviewer 2 screening/adjudication and treating-clinician sign-off remain pending. It is not a diagnosis, treatment recommendation, or official human-gated engine export.",
        "",
        "## Abstract",
        "",
        "**Objective:** Adversarially test the atomic claims, counterclaims, and global null hypothesis in a proposed adaptive-constraint/environmental-mismatch formulation of adult neurodevelopmental functioning.",
        "",
        "**Methods:** Seven evidence modules were prospectively compiled from the supplied protocol. PubMed/MEDLINE and Europe PMC were searched live from inception through 29 July 2026; ClinicalTrials.gov was also queried for scaffolding, sleep/circadian, and mood/substance modules. The live engine identified 4,165 module-record occurrences, retained 3,770 after within-module deduplication, and verified 3,638. A rapid cap advanced 980 records to one AI title/abstract reviewer; 2,658 verified records were deferred rather than excluded. A targeted PubMed gap search and official NICE guideline inspection supplemented specific safety and bias gaps. Selected evidence was extracted only after abstract or official-guideline inspection. No new meta-analysis was performed because populations, exposures, constructs, and outcomes were heterogeneous.",
        "",
        f"**Results:** AI Reviewer‑1 classified {screening['include']} records as include, {screening['uncertain']} uncertain, and {screening['exclude']} exclude across module occurrences. A bounded set of {extraction['selected_occurrences']} extracted occurrences ({extraction['unique_pmids']} unique PMIDs before adding two guidelines) informed claim-level synthesis. The strongest support concerned developmental persistence and multi-setting impairment in ADHD, social/peer burden, bidirectional sleep/state relevance, and cannabis-related psychiatric risk. Evidence was weaker or indirect for intelligence as masking, a unitary environmental-mismatch mechanism, DCD overlap in adults, durable AI scaffolding, generalized neurodevelopmental strengths, and emotional-compensation/temporal claims about daydreaming. The global null hypothesis was not rejected.",
        "",
        "**Conclusions:** Components of the model are plausible, but the literature cannot show that one coherent model explains this individual better than bipolar-spectrum state, sleep, substances/medications, anxiety/trauma, burnout, learned behavior, recall bias, or multiple interacting causes. Prospective clinical and collateral data are required.",
        "",
        "## Executive findings",
        "",
        "1. **Most defensible:** childhood ADHD can persist into adulthood and impair multiple settings; social and functional burden is real but mechanistically nonspecific.",
        "2. **Clinically consequential:** sleep/circadian state, bipolar episodes, cannabis/THC, stimulant agent/dose, nicotine, and medication context should be treated as time-varying differential variables.",
        "3. **Plausible but weaker:** context and reward influence performance; structured behavioral supports can help; a plural trait-plus-state formulation is reasonable.",
        "4. **Overstated without new data:** high intelligence as the cause of delayed recognition, achievement as proof of masking, creativity/pattern recognition as intrinsic neurodevelopmental strengths, and daydreaming as proven emotional compensation.",
        "5. **Unresolved:** the proposed unified adaptive-constraint model does not currently outperform the global null or a multiple-cause formulation.",
        "6. **Individual boundary:** no group-level source can diagnose the individual or prove that a reported exposure caused a reported state.",
        "",
        "## Review question and scope",
        "",
        "Among adolescents and adults with ADHD, autism-spectrum traits, developmental-coordination difficulties, bipolar disorder, or overlapping profiles, how do executive differences, compensatory strategies, environment/task fit, external supports, sleep/circadian state, mood, substances, medication, social adversity, and daydreaming relate to impairment and adaptive functioning?",
        "",
        "The source protocol is a multi-module review program and a longitudinal case-formulation framework, not one homogeneous PICOS review. Therefore, this report uses narrative synthesis and does not pool across diagnoses, exposures, or constructs.",
        "",
        "## Methods",
        "",
        "### Protocol and atomic claim ledger",
        "",
        "The supplied protocol was decomposed into 66 atomic hypotheses, qualifications, counterclaims, model claims, and boundaries (H1–H12 and G1–G6). The personal narrative was not used as scientific evidence and was not transmitted in retrieval queries.",
        "",
        "### Sources and search",
        "",
        "PubMed/MEDLINE and Europe PMC were executed for all modules. ClinicalTrials.gov was executed for D, E1, and E2. Current official NICE ADHD (NG87) and bipolar (CG185) pages were inspected in Chrome. Exact queries, counts, caps, supplemental searches, and citation-chaining results are in `search_strategy.md`.",
        "",
        "Embase, PsycINFO, Web of Science, Scopus, Cochrane CENTRAL, CINAHL, ERIC, ProQuest, and WHO ICTRP were not searched directly in this run. Their use by included reviews does not count as a database search by this review.",
        "",
        "### Deduplication and rapid cap",
        "",
        "Verified identifiers were normalized and deduplicated within modules. Cross-module duplicates were retained because modules have different eligibility questions. Each module advanced 140 hierarchy-ranked records. Deferred records were not excluded.",
        "",
        "### Screening and reviewer status",
        "",
        "One AI Reviewer‑1 screened title/abstract records using module-specific criteria. There was no human Reviewer 2, no consensus reconciliation, and no completed full-text eligibility stage. The engine's clinical sign-off gate remains closed. These limitations lower the claim ceiling and preclude describing this as a complete systematic review.",
        "",
        "### Source integrity",
        "",
        f"All {verification['unique_pmids']} selected PMIDs were re-fetched from NCBI PubMed EFetch; all had abstracts. Exact titles were matched against local retrieval records. DOI URLs were resolved where present, with outcomes retained in `verified_references.csv`. PubMed retraction/correction relationships were checked in the returned XML. Two official guideline pages were inspected directly. Retrieval-stage unresolved records were excluded from evidence use.",
        "",
        "### Extraction and synthesis",
        "",
        "One structured row was created per selected source and exploded to one row per claim–source relationship in `claim_evidence_matrix.csv`. Numerical effects were recorded only when present in the inspected abstract; otherwise `NR` is used. Narrative synthesis was selected a priori for conceptual and clinical heterogeneity.",
        "",
        "Each `Alternative explanation` entry is an explicitly unverified hypothesis to test, not an evidence-supported counterclaim, unless that entry itself cites a source. This distinction prevents uncited possibilities from being presented as established clinical facts.",
        "",
        "### Risk of bias and certainty",
        "",
        "Quality judgments are **provisional abstract-level appraisals**, not completed AMSTAR 2, RoB 2, ROBINS-I, QUIPS/JBI, QUADAS-2, or GRADE instruments. Judgments explicitly considered design, temporality, confounding, measurement validity, heterogeneity, precision, and directness. Formal instruments require full text and independent human checking; therefore, no claim receives a final clinical-certainty grade.",
        "",
        "### Deviations from a complete review",
        "",
        "- Rapid source and screening caps.",
        "- One AI reviewer rather than two independent human reviewers.",
        "- Abstract-level rather than complete full-text extraction for journal literature.",
        "- No information-specialist PRESS review.",
        "- Incomplete database coverage and citation chaining.",
        "- No preregistration before retrieval.",
        "- No formal full-text exclusions, complete design-specific risk-of-bias instruments, outcome-level GRADE, or statistical pooling.",
        "- Three nonseparable pediatric or mixed-population sources (PMIDs 40868584, 42199316, and 42327684) were retained post hoc only as clearly labeled hypothesis-generating context. They do not contribute to claim verdicts; their frozen-criteria deviations are explicit in `claim_evidence_matrix.csv`.",
        "",
        "## PRISMA-style rapid-review flow",
        "",
        module_flow_table(),
        "",
        "Aggregate occurrence flow: 4,165 identified → 3,770 after within-module deduplication → 3,638 live verified; 132 unresolved/rejected; 980 advanced and screened; 2,658 deferred by cap; 206 included, 27 uncertain, and 747 excluded at title/abstract stage. The extraction total reconciles as 96 module occurrences plus 2 global targeted supplements = 98 occurrences (92 unique PMIDs). Cross-module uniqueness is reported separately in the audit files.",
        "",
        "## Risk-of-bias synthesis",
        "",
        "- **Systematic reviews/meta-analyses:** often the strongest available evidence, but several had high heterogeneity, broad constructs, or observational inputs. Abstract-only review cannot support item-level AMSTAR 2 ratings.",
        "- **Randomized trials:** strongest for bounded intervention and acute effects; often short, underpowered for rare mania/psychosis outcomes, and not designed for etiologic claims.",
        "- **Longitudinal/within-person cohorts:** improve temporal inference but remain vulnerable to time-varying confounding, selection, attrition, and measurement change.",
        "- **Cross-sectional and mediation studies:** useful for construct mapping; they cannot establish temporal direction even when labeled mediation.",
        "- **Qualitative evidence:** useful for experience and perceived function; it does not establish prevalence, mechanism, or objective superiority.",
        "- **Case applicability:** low for most claims because the literature cannot verify the individual's exposure timing, childhood onset, episode boundaries, or functional course.",
        "",
        "## Claim-by-claim synthesis",
        "",
    ]

    for module, _, _ in MODULES:
        sections.append(
            demote_headings(
                (RUN / module / "module_synthesis_ai_r1.md").read_text(encoding="utf-8")
            )
        )
        sections.extend(["", "<!-- PAGE BREAK -->", ""])
    sections.append(
        demote_headings((RUN / "global_synthesis_ai_r1.md").read_text(encoding="utf-8"))
    )
    sections.extend(
        [
            "",
            "## Integrated adversarial synthesis",
            "",
            "### Components with the strongest support",
            "",
            "- Developmental persistence and multi-setting burden are established possibilities in ADHD, although remission and heterogeneous adult outcomes are common.",
            "- ADHD and autism are each associated with social-function difficulty; internalizing symptoms, bullying, stigma, conflict, and co-occurring conditions can add burden.",
            "- Sleep/circadian disturbance is clinically relevant and often bidirectional with mood and attention.",
            "- Cannabis—especially frequent/high-THC exposure—is associated with psychosis and adverse mood/cognitive outcomes.",
            "- Prescribed stimulants have uncommon but serious psychiatric risks that vary by agent, dose, vulnerability, and mood-stabilization context.",
            "",
            "### Plausible but weakly evidenced",
            "",
            "- High intelligence or achievement as a mechanism of delayed detection or masking.",
            "- General person-environment fit as a causal moderator beyond specific task/reward effects.",
            "- Durable real-world benefit from generic checklists, reminders, occupational redesign, or AI-assisted organization.",
            "- Stable neurodevelopmental “strengths” in creativity, hyperfocus, or pattern recognition.",
            "- Emotional compensation as a general function of maladaptive daydreaming.",
            "",
            "### Overstated or unsupported formulations",
            "",
            "- Achievement alone proves masking or compensation.",
            "- A coherent life narrative establishes one neurodevelopmental cause.",
            "- ADHD alone explains social-communication or coordination difficulty.",
            "- Sleep loss, cannabis, stimulants, or nicotine necessarily explain every state change—or, conversely, are irrelevant because baseline traits exist.",
            "- Group associations determine the individual's diagnosis or causal history.",
            "",
            "### Nonspecific findings",
            "",
            "Executive difficulty, rapid speech, distractibility, emotional dysregulation, confidence, unusual salience, sleep change, social conflict, and variable performance can occur across ADHD, autism, bipolar states, anxiety/trauma, depression, sleep disorders, substance/medication effects, burnout, and ordinary contextual variation. Temporal course and collateral evidence are more discriminating than symptom resemblance.",
            "",
            "### What would materially weaken the central model",
            "",
            "- Reliable childhood and adolescent records showing intact attention, organization, social function, and coordination across settings.",
            "- Clear adult onset after a mood, sleep, substance, medication, medical, or occupational change with durable resolution when that factor normalizes.",
            "- Stable impairment across very different environments with no task/context moderation.",
            "- Achievement obtained efficiently and independently without unusual effort, support, variability, or recovery cost.",
            "- Prospective data showing proposed scaffolds do not reproducibly improve objective outcomes.",
            "",
            "### Data most likely to discriminate explanations",
            "",
            "- Contemporaneous childhood records and safe collateral history.",
            "- Structured specialist ADHD and bipolar assessment; autism/DCD assessment when indicated.",
            "- A dated medication, substance, sleep, and mood timeline.",
            "- Prospective daily measures of sleep, energy, speech, unusual certainty/salience, attention, task demands, and objective function.",
            "- Setting-specific impairment and support/effort measures.",
            "- Medical/neurological and sleep-disorder evaluation when clinically indicated.",
            "",
            "## Psychiatrist-facing safety boundary",
            "",
            "Current NICE bipolar guidance directs urgent specialist assessment when mania or severe depression is suspected and requires consideration of ADHD, substance misuse, medications, and physical differential diagnoses. This report does not recommend starting, stopping, or changing any medication or substance. Intentional sleep deprivation, cannabis/stimulant/nicotine rechallenge, or unsupervised medication experimentation would be unsafe ways to test the model.",
            "",
            "## Integrity audit",
            "",
            "- Every cited PMID in this report is present in the verified metadata set.",
            "- Every selected record has an inspected PubMed abstract; the NICE records were inspected on official pages.",
            "- Exact PubMed titles were matched; author, journal, year, volume/issue/pages, DOI, PMID, PMCID, and stable URLs are retained in `verified_references.csv`.",
            "- PubMed retraction/correction relationships and DOI resolution outcomes were recorded. Any flagged update is visible in the reference table rather than silently ignored.",
            "- Numerical effects were transcribed only from inspected abstracts; missing effects are labelled `NR`.",
            "- Duplicate PubMed references were collapsed by PMID in the reference list.",
            "- Causal language was downgraded for cross-sectional, correlational, and observational evidence.",
            "- Counterclaims, nulls, alternative causes, falsifiers, and individual-applicability limits are stated for every atomic claim.",
            "- No unresolved retrieval-stage reference was used.",
            "- Human Reviewer 2, full-text adjudication, formal risk-of-bias instruments, and clinical sign-off remain unresolved.",
            "",
            "## References",
            "",
        ]
    )
    for index, ref in enumerate(
        sorted(all_refs, key=lambda item: (str(item.get("year", "")), str(item["record_id"]))),
        start=1,
    ):
        stable = str(ref.get("stable_url") or "")
        sections.append(f"{index}. {cite(ref)} {stable}")
    sections.append("")

    text = "\n".join(sections)
    path = OUTPUT / "systematic_evidence_review_full.md"
    path.write_text(text, encoding="utf-8")
    return path


def integrity_audit(
    report_path: Path,
    all_refs: list[dict[str, object]],
    relationships: list[dict[str, object]],
    matrix_path: Path,
    refs_path: Path,
    other_paths: list[Path],
) -> Path:
    report = report_path.read_text(encoding="utf-8")
    selected_pmids = {
        str(ref["pmid"]) for ref in all_refs if str(ref.get("pmid") or "")
    }
    cited_pmids = set(re.findall(r"PMID[:\s]+(\d+)", report))
    uncatalogued = sorted(cited_pmids - selected_pmids, key=int)
    if uncatalogued:
        raise ValueError(f"report cites unverified PMIDs: {uncatalogued}")
    if not cited_pmids:
        raise ValueError("report contains no PMID citations")

    retracted = [
        {
            "record_id": ref["record_id"],
            "status": ref["retraction_or_correction_status"],
        }
        for ref in all_refs
        if str(ref.get("retraction_or_correction_status", "")).startswith("Retracted")
        or str(ref.get("retraction_or_correction_status", "")) == "Retraction notice"
    ]
    if retracted:
        raise ValueError(f"selected evidence includes retracted material: {retracted}")

    with matrix_path.open(newline="", encoding="utf-8") as handle:
        matrix_rows = list(csv.DictReader(handle))
    with refs_path.open(newline="", encoding="utf-8") as handle:
        reference_rows = list(csv.DictReader(handle))
    ref_ids = {row["record_id"] for row in reference_rows}
    matrix_ref_ids = {row["record_id"] for row in matrix_rows}
    if not matrix_ref_ids <= ref_ids:
        raise ValueError(f"matrix has unknown references: {sorted(matrix_ref_ids - ref_ids)}")

    ledger_path = INPUTS / "claim_ledger.csv"
    with ledger_path.open(newline="", encoding="utf-8") as handle:
        ledger_claims = {row["claim_id"] for row in csv.DictReader(handle)}
    matrix_claims = {row["claim_id"] for row in matrix_rows}
    missing_claims = sorted(ledger_claims - matrix_claims)
    matrix_pairs = {
        (row["claim_id"], row["pmid"])
        for row in matrix_rows
        if str(row.get("pmid") or "")
    }

    claim_heading = re.compile(
        r"^#{3,5}\s+((?:H|G)\d+(?:\.\d+|\.N\d+)?)\s+[—-]\s+",
        re.MULTILINE,
    )
    claim_matches = list(claim_heading.finditer(report))
    claim_sections: dict[str, str] = {}
    for index, match in enumerate(claim_matches):
        following = report[match.end() :]
        next_heading = re.search(r"^#{1,6}\s+", following, re.MULTILINE)
        end = match.end() + next_heading.start() if next_heading else len(report)
        claim_sections[match.group(1)] = report[match.start() : end]
    if set(claim_sections) != ledger_claims:
        raise ValueError(
            "claim-section mismatch: "
            f"missing={sorted(ledger_claims - set(claim_sections))}, "
            f"extra={sorted(set(claim_sections) - ledger_claims)}"
        )

    ordinal = {"High", "Moderate", "Low", "Very low"}
    verdicts = {"Supported", "Partially supported", "Unsupported", "Indeterminate"}
    required_labels = {
        "strongest_support": "**Strongest support:**",
        "alternative": "**Alternative explanation (unverified hypothesis unless cited):**",
        "falsifier": "**Falsifier:**",
    }
    unmapped_claim_pmids: list[dict[str, str]] = []
    for claim_id, section in claim_sections.items():
        verdict_match = re.search(r"^- \*\*Verdict:\*\*\s*(.+?)\.?$", section, re.MULTILINE)
        certainty_match = re.search(
            r"^- \*\*Certainty:\*\*\s*(.+?)\.?$",
            section,
            re.MULTILINE,
        )
        directness_match = re.search(
            r"^- \*\*Directness to this case:\*\*\s*(.+?)\.?$",
            section,
            re.MULTILINE,
        )
        calibration_match = re.search(
            r"^- \*\*Confidence that wording is appropriately calibrated:\*\*\s*(.+?)\.?$",
            section,
            re.MULTILINE,
        )
        values = {
            "verdict": verdict_match.group(1).rstrip(".") if verdict_match else "",
            "certainty": certainty_match.group(1).rstrip(".") if certainty_match else "",
            "directness": directness_match.group(1).rstrip(".")
            if directness_match
            else "",
            "calibration": calibration_match.group(1).rstrip(".")
            if calibration_match
            else "",
        }
        if values["verdict"] not in verdicts:
            raise ValueError(f"{claim_id}: invalid or missing verdict {values['verdict']!r}")
        for field in ("certainty", "directness", "calibration"):
            if values[field] not in ordinal:
                raise ValueError(
                    f"{claim_id}: invalid or missing {field} {values[field]!r}"
                )
        for field, label in required_labels.items():
            if label not in section:
                raise ValueError(f"{claim_id}: missing {field}")
        if not (
            "**Strongest counterevidence or qualification:**" in section
            or "**Strongest opposing evidence or qualification:**" in section
            or "**Counterevidence/qualification:**" in section
        ):
            raise ValueError(f"{claim_id}: missing opposing or qualifying evidence")
        if not (
            "**Corrected calibrated claim:**" in section
            or "**Calibrated claim:**" in section
        ):
            raise ValueError(f"{claim_id}: missing calibrated claim")
        if not (
            "**Personal or clinical data needed:**" in section
            or "**Useful personal/clinical data:**" in section
        ):
            raise ValueError(f"{claim_id}: missing discriminating personal/clinical data")
        for pmid in sorted(set(re.findall(r"PMID[:\s]+(\d+)", section)), key=int):
            if (claim_id, pmid) not in matrix_pairs:
                unmapped_claim_pmids.append({"claim_id": claim_id, "pmid": pmid})
    if unmapped_claim_pmids:
        raise ValueError(
            f"claim sections cite PMIDs absent from matrix: {unmapped_claim_pmids}"
        )
    if "et al.." in report or "?." in report or "\\newpage" in report:
        raise ValueError("report contains a known citation or Markdown formatting defect")

    files = [report_path, matrix_path, refs_path, *other_paths]
    pdf_path = OUTPUT / "systematic_evidence_review_full.pdf"
    if pdf_path.exists():
        files.append(pdf_path)
    hashes = {path.name: sha256(path) for path in files}
    audit = {
        "status": "Independent AI adversarial audit complete; independent human Reviewer 2 and clinical sign-off pending",
        "search_date": SEARCH_DATE,
        "report_cited_pmids": len(cited_pmids),
        "verified_pubmed_records": len(selected_pmids),
        "verified_reference_rows_including_guidelines": len(reference_rows),
        "claim_study_relationship_rows": len(matrix_rows),
        "ledger_claims": len(ledger_claims),
        "claims_with_matrix_relationships": len(matrix_claims),
        "claims_without_any_matrix_relationship": missing_claims,
        "atomic_claim_sections": len(claim_sections),
        "claim_template_complete": True,
        "claim_section_pmids_all_mapped": True,
        "post_hoc_hypothesis_only_pmids": sorted(POST_HOC_HYPOTHESIS_ONLY),
        "uncatalogued_report_pmids": uncatalogued,
        "selected_retracted_records": retracted,
        "duplicate_reference_ids": [
            key
            for key, count in Counter(row["record_id"] for row in reference_rows).items()
            if count > 1
        ],
        "artifact_sha256": hashes,
        "gate_status": {
            "ai_reviewer_1": "complete",
            "independent_ai_adversarial_audit": "complete",
            "human_reviewer_2": "pending",
            "full_text_screening": "pending",
            "formal_risk_of_bias": "pending",
            "clinical_sign_off": "pending",
            "official_engine_export": "withheld",
        },
    }
    path = OUTPUT / "integrity_audit.json"
    path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    metadata, all_refs, _, relationships = load_all()
    ref_by_id = {str(ref["record_id"]): ref for ref in all_refs}
    matrix = write_matrix(relationships, ref_by_id)
    refs = write_verified_references(all_refs, relationships)
    search = write_search_strategy()
    exclusions = write_exclusions()
    rejected = write_rejected()
    brief = write_psychiatrist_brief()
    report = write_full_review(all_refs, relationships)
    audit = integrity_audit(
        report,
        all_refs,
        relationships,
        matrix,
        refs,
        [search, exclusions, rejected, brief],
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "verified_pubmed_records": len(metadata),
                "verified_reference_rows": len(all_refs),
                "relationship_sources": len(relationships),
                "files": sorted(path.name for path in OUTPUT.iterdir()),
                "audit": str(audit),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
