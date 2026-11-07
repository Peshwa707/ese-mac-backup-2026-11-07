#!/usr/bin/env python3
"""Build a reproducible, non-clinical index over a DSM-5/PubMed run.

The experiment deliberately references the governed run instead of copying or
rewriting it.  Every source file is catalogued with a size and SHA-256 hash so
downstream analysis can be rerun against the exact same inputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = REPO_ROOT / "runs/dsm5-nd-baseline-20260730"
EXPERIMENT_DIRNAME = "data_science_experiment"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def json_len(path: Path) -> int | None:
    value = read_json(path)
    return len(value) if isinstance(value, (list, dict)) else None


def json_list_len(path: Path, key: str) -> int | None:
    value = read_json(path)
    rows = value.get(key) if isinstance(value, dict) else None
    return len(rows) if isinstance(rows, list) else None


def iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from sorted(p for p in path.rglob("*") if p.is_file())


def source_path(root: Path, path: Path) -> tuple[str, str]:
    """Return a stable source root label and path relative to that root."""
    try:
        return "run", path.relative_to(root).as_posix()
    except ValueError:
        return "repo", path.relative_to(REPO_ROOT).as_posix()


def add_sources(catalog: list[dict[str, Any]], root: Path, source_type: str,
                paths: Iterable[Path], notes: str = "") -> None:
    seen: set[Path] = {Path(row["absolute_path"]) for row in catalog}
    for candidate in paths:
        for path in iter_files(candidate):
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            label, relative = source_path(root, path)
            catalog.append({
                "source_type": source_type,
                "source_root": label,
                "path": relative,
                "absolute_path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "notes": notes,
            })


def load_screening(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_json(path, [])
    return {str(row.get("pmid")): row for row in rows if isinstance(row, dict) and row.get("pmid")}


def build_pubmed_index(run: Path, output: Path) -> dict[str, Any]:
    corpus = read_json(run / "corpus.json", {})
    base_rows = corpus.get("verified") or corpus.get("deduped") or corpus.get("identified") or []
    agent_rows = {str(row.get("pmid")): row for row in (read_json(run / "records_for_agents.json", []) or [])
                  if isinstance(row, dict) and row.get("pmid")}
    r1 = load_screening(run / "screening_r1.json")
    r2 = load_screening(run / "screening_r2.json")
    screening = load_screening(run / "screening.json")
    fulltext = {str(row.get("pmid")): row for row in (read_json(run / "ai_fulltext_draft/fulltext_records.json", []) or [])
                if isinstance(row, dict) and row.get("pmid")}

    fields = [
        "pmid", "title", "year", "doi", "url", "study_type", "source", "verified", "verification",
        "abstract", "screening_decision", "screening_confidence", "screening_reason",
        "codex_decision", "codex_confidence", "claude_decision", "claude_confidence",
        "fulltext_available", "fulltext_chars", "fulltext_source", "fulltext_file", "fulltext_sha256",
    ]
    rows: list[dict[str, Any]] = []
    for record in base_rows:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        pmid = str(record["id"])
        agent = agent_rows.get(pmid, {})
        one = screening.get(pmid, {})
        one_r1 = r1.get(pmid, {})
        one_r2 = r2.get(pmid, {})
        ft = fulltext.get(pmid, {})
        row = {
            "pmid": pmid,
            "title": record.get("title", ""),
            "year": record.get("year", ""),
            "doi": record.get("doi", ""),
            "url": record.get("url", ""),
            "study_type": record.get("study_type", ""),
            "source": record.get("source", ""),
            "verified": record.get("verified", ""),
            "verification": record.get("verification", ""),
            "abstract": agent.get("abstract", record.get("snippet", "")),
            "screening_decision": one.get("decision", ""),
            "screening_confidence": one.get("confidence", ""),
            "screening_reason": one.get("reason", ""),
            "codex_decision": one_r1.get("decision", ""),
            "codex_confidence": one_r1.get("confidence", ""),
            "claude_decision": one_r2.get("decision", ""),
            "claude_confidence": one_r2.get("confidence", ""),
            "fulltext_available": ft.get("available", ""),
            "fulltext_chars": ft.get("chars", ""),
            "fulltext_source": ft.get("source", ""),
            "fulltext_file": ft.get("full_text_file", ""),
            "fulltext_sha256": ft.get("full_text_sha256", ""),
        }
        rows.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "path": str(output),
        "rows": len(rows),
        "fields": fields,
        "screening_r1_rows": len(r1),
        "screening_r2_rows": len(r2),
        "fulltext_rows": len(fulltext),
    }


def write_readme(experiment: Path) -> None:
    text = """# DSM-5 neurodevelopmental data-science experiment

`experiment_type: exploratory_nonclinical_data_science`
`review_mode: autonomous_two_ai_exploratory`

This is a separate, reproducibility-oriented analysis lane over an existing
DSM-5/PubMed evidence run and an exploratory Reddit sidecar. It is not a
systematic-review conclusion, diagnosis, treatment recommendation, prevalence
estimate, or clinical sign-off. Reddit posts are self-reports from a selected
online population and the current collection is rate-limited/incomplete; they
must not be interpreted as population frequencies or efficacy evidence.

The experiment is reference-based: `source_catalog.csv` records every source
artifact, its source root, byte size, and SHA-256 hash. `pubmed_index.csv` is a
joinable row-level index across the PubMed corpus, screening outputs, and
full-text availability. Raw source artifacts remain in the governed run and
are not rewritten here.

Within this experiment, human review and clinical sign-off are out of scope by
protocol: outputs are limited to autonomous two-AI data-science analysis. This
does not authorize clinical interpretation or governed export. The referenced
governed run remains separate and retains its own pending gates.
"""
    (experiment / "README.md").write_text(text, encoding="utf-8")


def build(run: Path, experiment_name: str = EXPERIMENT_DIRNAME) -> dict[str, Any]:
    run = run.resolve()
    experiment = run / experiment_name
    experiment.mkdir(parents=True, exist_ok=True)
    write_readme(experiment)

    pubmed_index = build_pubmed_index(run, experiment / "pubmed_index.csv")
    catalog: list[dict[str, Any]] = []
    root_files = [run / name for name in [
        "corpus.json", "search_manifest.json", "records_for_agents.json", "to_screen.json",
        "prescreen.json", "screening.json", "screening_r1.json", "screening_r2.json",
        "reconcile.json", "run_state.json",
    ]]
    add_sources(catalog, run, "pubmed_corpus_and_screening", root_files,
                "PubMed search, corpus, screening, and AI-lane reconciliation artifact")
    add_sources(catalog, run, "pubmed_screening_codex", [run / "screening_codex_cli"],
                "Codex screening lane raw and parsed artifacts")
    add_sources(catalog, run, "pubmed_screening_claude", [run / "screening_claude_cli"],
                "Claude screening lane raw and parsed artifacts")
    add_sources(catalog, run, "pubmed_fulltext", [run / "ai_fulltext_draft"],
                "Full-text retrieval, prompts, provider outputs, and draft-only artifacts")
    add_sources(catalog, run, "reddit_exploratory", [run / "reddit_symptom_frequency"],
                "Exploratory Reddit sidecar; incomplete/rate-limited and non-prevalence")
    add_sources(catalog, run, "dsm5_taxonomy_and_configuration", [
        REPO_ROOT / "review_inputs/dsm5-neurodevelopmental-criteria-mechanisms",
        REPO_ROOT / "verticals/dsm5-neurodevelopmental-criteria-mechanisms",
    ], "DSM-5 criteria crosswalk, domains, protocol, extraction, and grading configuration")
    add_sources(catalog, run, "governance_reference", [run / "HUMAN_REVIEW_HANDOFF.md", run / "human_review_package"],
                "Reference-only governance artifacts; human gates remain pending")

    catalog_path = experiment / "source_catalog.csv"
    fields = ["source_type", "source_root", "path", "bytes", "sha256", "notes"]
    with catalog_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(catalog, key=lambda item: (item["source_type"], item["source_root"], item["path"])):
            writer.writerow({key: row[key] for key in fields})

    reddit_manifest = read_json(run / "reddit_symptom_frequency/manifest.json", {}) or {}
    reddit_checkpoint = run / "reddit_symptom_frequency/checkpoint.jsonl"
    governed_state = read_json(run / "run_state.json", {}) or {}
    manifest = {
        "schema_version": "1.0",
        "experiment_type": "exploratory_nonclinical_data_science",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run.name,
        "source_run": str(run),
        "source_catalog": "source_catalog.csv",
        "derived_indexes": {"pubmed_index": pubmed_index},
        "counts": {
            "corpus_verified": json_list_len(run / "corpus.json", "verified"),
            "to_screen": json_len(run / "to_screen.json"),
            "records_for_agents": json_len(run / "records_for_agents.json"),
            "screening_r1": json_len(run / "screening_r1.json"),
            "screening_r2": json_len(run / "screening_r2.json"),
            "fulltext_records": json_len(run / "ai_fulltext_draft/fulltext_records.json"),
            "source_catalog_files": len(catalog),
            "reddit_checkpoint_rows": sum(1 for _ in reddit_checkpoint.open(encoding="utf-8")) if reddit_checkpoint.exists() else 0,
        },
        "dsm5_configuration": {
            "review_inputs": "../../review_inputs/dsm5-neurodevelopmental-criteria-mechanisms",
            "vertical": "../../verticals/dsm5-neurodevelopmental-criteria-mechanisms",
        },
        "reddit_sidecar": {
            "collection_status": reddit_manifest.get("collection_status"),
            "stop_reason": reddit_manifest.get("stop_reason"),
            "claim_ceiling": reddit_manifest.get("claim_ceiling"),
            "window_start_utc": reddit_manifest.get("window_start_utc"),
            "window_end_utc_exclusive": reddit_manifest.get("window_end_utc_exclusive"),
            "subreddits": reddit_manifest.get("subreddits", []),
        },
        "governance": {
            "review_mode": "autonomous_two_ai_exploratory",
            "human_review_required_within_experiment": False,
            "clinical_signoff_required_within_experiment": False,
            "human_reviewer_2": "pending",
            "clinical_signoff": "pending",
            "governed_run_state": governed_state.get("state"),
            "this_experiment_can_unlock_gates": False,
        },
    }
    (experiment / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", nargs="?", type=Path, default=DEFAULT_RUN)
    parser.add_argument(
        "--experiment-name",
        default=EXPERIMENT_DIRNAME,
        help="Directory name under the run for the new experiment.",
    )
    args = parser.parse_args()
    manifest = build(args.run_dir, args.experiment_name)
    print(json.dumps({"experiment": str(args.run_dir / args.experiment_name), "counts": manifest["counts"]}, indent=2))


if __name__ == "__main__":
    main()
