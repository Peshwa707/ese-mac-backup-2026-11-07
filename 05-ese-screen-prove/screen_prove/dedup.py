"""Deterministic publication-level deduplication and PRISMA counts.

Every record `deduplicate` accepts must be a dict carrying a non-empty
string `record_id` -- the same minimum shape every downstream stage
(`screening.screen_records`, `replay.verify_replay`,
`contract.verify_deduplicated_corpus`) already assumes without re-checking
it. A record that does not meet that minimum shape is rejected here,
deliberately and with an explicit `IntegrityError`, rather than being kept
silently (which would only surface later as an unrelated `KeyError`/
`AttributeError` deep in a downstream stage).
"""

from __future__ import annotations

import re
from typing import Any

from .contracts import IntegrityError, object_hash


def _norm_title(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", value.lower())).strip()


def _keys(record: dict[str, Any]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if record.get("pmid"):
        keys.append(("pmid", str(record["pmid"]).strip()))
    if record.get("doi"):
        keys.append(("doi", str(record["doi"]).strip().lower()))
    title = _norm_title(str(record.get("title", "")))
    if title:
        keys.append(("title_year", f"{title}|{record.get('year', '')}"))
    return keys


def deduplicate(corpus: dict[str, Any]) -> dict[str, Any]:
    body = {key: corpus[key] for key in ("version", "source_name", "source_file", "source_hash", "records")
            if key in corpus}
    if corpus.get("corpus_hash") != object_hash(body):
        raise IntegrityError("corpus_hash does not match frozen corpus content")
    kept: list[dict[str, Any]] = []
    decisions: list[dict[str, str]] = []
    seen: dict[tuple[str, str], str] = {}
    for record in corpus.get("records", []):
        record_id = record.get("record_id") if isinstance(record, dict) else None
        if not isinstance(record_id, str) or not record_id.strip():
            raise IntegrityError("every corpus record must be a dict with a non-empty record_id")
        duplicate_key = next(((kind, value) for kind, value in _keys(record) if (kind, value) in seen), None)
        if duplicate_key:
            kept_id = seen[duplicate_key]
            decisions.append({
                "record_id": record["record_id"],
                "kept_record_id": kept_id,
                "reason": f"duplicate_{duplicate_key[0]}",
            })
            continue
        kept.append(record)
        for key in _keys(record):
            seen[key] = record["record_id"]
    return {
        "version": "dedup-v1",
        "source_hash": corpus["source_hash"],
        "corpus_hash": corpus["corpus_hash"],
        "records": kept,
        "records_hash": object_hash({"records": kept}),
        "decisions": decisions,
        "counts": {
            "identified": len(corpus.get("records", [])),
            "after_dedup": len(kept),
            "duplicates_removed": len(decisions),
        },
    }
