#!/usr/bin/env python3
"""Fetch and verify bibliographic metadata for selected evidence records.

The source of truth is NCBI PubMed EFetch. DOI URLs are resolved separately
when a DOI is present. The script refuses selected records lacking an inspected
local abstract.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
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
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
USER_AGENT = "EvidenceSynthesisEngine/0.1 (AI-assisted rapid evidence review; contact local-user)"


def node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return html.unescape("".join(node.itertext())).strip()


def first_text(node: ET.Element, paths: list[str]) -> str:
    for path in paths:
        value = node_text(node.find(path))
        if value:
            return value
    return ""


def normalized(text: str) -> str:
    return re.sub(r"\W+", "", html.unescape(text or "").casefold())


def load_selected() -> tuple[list[str], dict[str, list[str]]]:
    supplement_path = (
        ROOT
        / "review_inputs"
        / "neurodevelopmental-environmental-mismatch"
        / "targeted_pubmed_supplements.json"
    )
    supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
    supplemental_pmids = {
        str(pmid)
        for search in supplement["searches"]
        for pmid in search["selected_pmids"]
    }
    selected_by_module: dict[str, list[str]] = {}
    all_pmids: list[str] = []
    for module in MODULES:
        module_dir = RUN / module
        extraction_path = module_dir / "evidence_extraction_ai_r1.json"
        if not extraction_path.exists():
            raise FileNotFoundError(f"pending extraction: {extraction_path}")
        rows = json.loads(extraction_path.read_text(encoding="utf-8"))
        candidates = {
            str(row["pmid"]): row
            for row in json.loads(
                (module_dir / "screening_candidates.json").read_text(encoding="utf-8")
            )
        }
        selected: list[str] = []
        for row in rows:
            pmid = str(row["pmid"])
            if pmid not in candidates and pmid not in supplemental_pmids:
                raise ValueError(f"{module}: selected PMID {pmid} is outside candidates")
            if pmid in candidates and not str(candidates[pmid].get("abstract") or "").strip():
                raise ValueError(f"{module}: selected PMID {pmid} lacks inspected abstract")
            selected.append(pmid)
            all_pmids.append(pmid)
        selected_by_module[module] = selected
    global_path = RUN / "global_evidence_extraction_ai_r1.json"
    global_rows = json.loads(global_path.read_text(encoding="utf-8"))
    global_pmids: list[str] = []
    for row in global_rows:
        pmid = str(row["pmid"])
        if not row.get("abstract_available") or row.get("evidence_basis") != "abstract":
            raise ValueError(f"global selected PMID {pmid} lacks abstract-level verification")
        global_pmids.append(pmid)
        all_pmids.append(pmid)
    selected_by_module["global"] = global_pmids
    return sorted(set(all_pmids), key=int), selected_by_module


def fetch_xml(pmids: list[str]) -> bytes:
    params = urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    )
    request = urllib.request.Request(f"{NCBI}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def parse_article(article: ET.Element) -> dict[str, object]:
    medline = article.find("MedlineCitation")
    article_node = medline.find("Article") if medline is not None else None
    pubmed_data = article.find("PubmedData")
    if medline is None or article_node is None:
        raise ValueError("malformed PubMedArticle")

    pmid = node_text(medline.find("PMID"))
    title = node_text(article_node.find("ArticleTitle"))
    abstract_parts: list[str] = []
    for part in article_node.findall("./Abstract/AbstractText"):
        label = (part.attrib.get("Label") or "").strip()
        text = node_text(part)
        abstract_parts.append(f"{label}: {text}" if label and text else text)
    abstract = "\n\n".join(part for part in abstract_parts if part)

    authors: list[str] = []
    for author in article_node.findall("./AuthorList/Author"):
        collective = node_text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        last = node_text(author.find("LastName"))
        fore = node_text(author.find("ForeName"))
        initials = node_text(author.find("Initials"))
        display = " ".join(part for part in (fore or initials, last) if part)
        if display:
            authors.append(display)

    journal = first_text(
        article_node,
        ["./Journal/Title", "./Journal/ISOAbbreviation"],
    )
    iso_journal = first_text(article_node, ["./Journal/ISOAbbreviation"])
    volume = first_text(article_node, ["./Journal/JournalIssue/Volume"])
    issue = first_text(article_node, ["./Journal/JournalIssue/Issue"])
    pages = first_text(article_node, ["./Pagination/MedlinePgn"])
    if not pages:
        for eloc in article_node.findall("./ELocationID"):
            if eloc.attrib.get("EIdType") in {"pii", "doi"}:
                pages = node_text(eloc)
                if pages:
                    break
    pubdate = article_node.find("./Journal/JournalIssue/PubDate")
    year = ""
    publication_date = ""
    if pubdate is not None:
        year = node_text(pubdate.find("Year"))
        month = node_text(pubdate.find("Month"))
        day = node_text(pubdate.find("Day"))
        medline_date = node_text(pubdate.find("MedlineDate"))
        publication_date = " ".join(part for part in (year, month, day) if part) or medline_date
        if not year and medline_date:
            match = re.search(r"(18|19|20)\d{2}", medline_date)
            year = match.group(0) if match else ""

    ids: dict[str, str] = {}
    if pubmed_data is not None:
        for item in pubmed_data.findall("./ArticleIdList/ArticleId"):
            id_type = item.attrib.get("IdType", "").lower()
            value = node_text(item)
            if id_type and value:
                ids[id_type] = value
    doi = ids.get("doi", "")
    if not doi:
        for eloc in article_node.findall("./ELocationID"):
            if eloc.attrib.get("EIdType") == "doi":
                doi = node_text(eloc)
                break
    pmcid = ids.get("pmc", "")

    publication_types = [
        node_text(item)
        for item in article_node.findall("./PublicationTypeList/PublicationType")
        if node_text(item)
    ]
    corrections: list[dict[str, str]] = []
    for item in medline.findall("./CommentsCorrectionsList/CommentsCorrections"):
        corrections.append(
            {
                "ref_type": item.attrib.get("RefType", ""),
                "pmid": node_text(item.find("PMID")),
                "note": node_text(item.find("Note")),
                "source": node_text(item.find("RefSource")),
            }
        )

    status = "No retraction/correction link found in PubMed record"
    lower_types = {item.casefold() for item in publication_types}
    ref_types = {item["ref_type"] for item in corrections}
    if "retracted publication" in lower_types or "RetractionIn" in ref_types:
        status = "Retracted or linked to a retraction in PubMed"
    elif "retraction of publication" in lower_types or "RetractionOf" in ref_types:
        status = "Retraction notice"
    elif ref_types & {"ErratumIn", "ErratumFor", "RepublishedIn", "UpdateIn", "UpdateOf"}:
        status = "Correction/update relationship present in PubMed"

    return {
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": doi,
        "title": title,
        "authors": authors,
        "author_string": "; ".join(authors),
        "journal": journal,
        "journal_abbreviation": iso_journal,
        "year": year,
        "publication_date": publication_date,
        "volume": volume,
        "issue": issue,
        "pages_or_article_number": pages,
        "publication_types": publication_types,
        "abstract": abstract,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "verification_source": "NCBI PubMed EFetch",
        "correction_relations": corrections,
        "retraction_or_correction_status": status,
    }


def resolve_doi(doi: str) -> dict[str, object]:
    if not doi:
        return {"status": "not_applicable", "http_status": None, "final_url": ""}
    url = f"https://doi.org/{urllib.parse.quote(doi, safe='/()')}"
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return {
                "status": "resolved",
                "http_status": response.status,
                "final_url": response.geturl(),
            }
    except urllib.error.HTTPError as exc:
        final_url = exc.geturl()
        status = "http_error"
        if exc.code in {401, 403, 405, 429} and not final_url.startswith("https://doi.org/"):
            status = "resolved_publisher_blocked"
        return {
            "status": status,
            "http_status": exc.code,
            "final_url": final_url,
        }
    except Exception as exc:  # network/SSL/timeout evidence retained, never hidden
        return {
            "status": "unresolved",
            "http_status": None,
            "final_url": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    pmids, selected_by_module = load_selected()
    chunks = [pmids[index : index + 150] for index in range(0, len(pmids), 150)]
    xml_payloads: list[bytes] = []
    for index, chunk in enumerate(chunks):
        if index:
            time.sleep(0.4)
        xml_payloads.append(fetch_xml(chunk))

    combined_root = ET.Element("PubmedArticleSet")
    for payload in xml_payloads:
        parsed = ET.fromstring(payload)
        for article in parsed.findall("PubmedArticle"):
            combined_root.append(article)
    xml_bytes = ET.tostring(combined_root, encoding="utf-8", xml_declaration=True)
    xml_path = RUN / "selected_pubmed_efetch.xml"
    xml_path.write_bytes(xml_bytes)

    records = [parse_article(article) for article in combined_root.findall("PubmedArticle")]
    by_pmid = {str(record["pmid"]): record for record in records}
    missing = sorted(set(pmids) - set(by_pmid), key=int)
    if missing:
        raise ValueError(f"PubMed did not return selected PMIDs: {missing}")
    abstract_missing = sorted(
        (pmid for pmid in pmids if not str(by_pmid[pmid]["abstract"]).strip()),
        key=int,
    )
    if abstract_missing:
        raise ValueError(f"selected PubMed records lack abstracts: {abstract_missing}")

    title_mismatches: list[dict[str, str]] = []
    for module, module_pmids in selected_by_module.items():
        if module == "global":
            continue
        candidates = {
            str(row["pmid"]): row
            for row in json.loads(
                (RUN / module / "screening_candidates.json").read_text(encoding="utf-8")
            )
        }
        for pmid in module_pmids:
            if pmid not in candidates:
                continue
            if normalized(candidates[pmid]["title"]) != normalized(str(by_pmid[pmid]["title"])):
                title_mismatches.append(
                    {
                        "module": module,
                        "pmid": pmid,
                        "candidate_title": candidates[pmid]["title"],
                        "pubmed_title": str(by_pmid[pmid]["title"]),
                    }
                )
    if title_mismatches:
        raise ValueError(f"PubMed title mismatches: {title_mismatches}")

    doi_values = sorted({str(record["doi"]) for record in records if record["doi"]})
    doi_results: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(resolve_doi, doi): doi for doi in doi_values}
        for future in as_completed(future_map):
            doi = future_map[future]
            doi_results[doi] = future.result()
    for record in records:
        record["doi_verification"] = doi_results.get(
            str(record["doi"]),
            {"status": "not_applicable", "http_status": None, "final_url": ""},
        )

    records.sort(key=lambda row: int(str(row["pmid"])))
    metadata_path = RUN / "selected_pubmed_metadata.json"
    metadata_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    status_counts: dict[str, int] = {}
    for record in records:
        status = str(record["doi_verification"]["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    retraction_flags = [
        {
            "pmid": record["pmid"],
            "status": record["retraction_or_correction_status"],
            "relations": record["correction_relations"],
        }
        for record in records
        if record["retraction_or_correction_status"]
        != "No retraction/correction link found in PubMed record"
    ]
    summary = {
        "status": "AI Reviewer-1 bibliographic verification; human Reviewer 2 pending",
        "selected_occurrences": sum(len(values) for values in selected_by_module.values()),
        "unique_pmids": len(pmids),
        "pubmed_records_returned": len(records),
        "records_with_abstracts": sum(bool(record["abstract"]) for record in records),
        "records_with_doi": len(doi_values),
        "doi_verification_status_counts": status_counts,
        "retraction_or_correction_flags": retraction_flags,
        "selected_by_module": selected_by_module,
        "xml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        "search_date": "2026-07-29",
    }
    (RUN / "selected_verification_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: summary[k] for k in (
        "selected_occurrences",
        "unique_pmids",
        "pubmed_records_returned",
        "records_with_abstracts",
        "records_with_doi",
        "doi_verification_status_counts",
    )}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
