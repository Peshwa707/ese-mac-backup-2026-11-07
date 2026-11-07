"""Frozen RIS/NBIB import with source-preserving provenance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contracts import IntegrityError, object_hash, sha256_bytes, write_json


def _record_id(index: int, source_hash: str) -> str:
    return f"record:{source_hash[:16]}:{index:06d}"


def _year(value: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", value or "")
    return match.group(0) if match else ""


def _finish(record: dict[str, str], index: int, source: str, filename: str, source_hash: str) -> dict[str, Any] | None:
    title = " ".join((record.get("title") or "").split())
    if not title:
        return None
    result = {
        "record_id": _record_id(index, source_hash),
        "title": title,
        "abstract": " ".join((record.get("abstract") or "").split()),
        "pmid": (record.get("pmid") or "").strip(),
        "doi": (record.get("doi") or "").strip(),
        "year": _year(record.get("year") or ""),
        "source": source,
        "provenance_file": filename,
        "source_hash": source_hash,
        "raw_index": index,
        "study_type": (record.get("study_type") or "").strip(),
    }
    return result


def parse_ris(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_tag = ""
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", line)
        if not match:
            if line.startswith(" ") and last_tag in {"title", "abstract"}:
                current[last_tag] = (current.get(last_tag, "") + " " + line.strip()).strip()
            continue
        tag, value = match.group(1), match.group(2).strip()
        last_tag = ""
        if tag == "TY":
            if current:
                records.append(current)
            current = {}
        elif tag in {"TI", "T1"}:
            current["title"] = (current.get("title", "") + " " + value).strip()
            last_tag = "title"
        elif tag in {"AB", "N2"}:
            current["abstract"] = (current.get("abstract", "") + " " + value).strip()
            last_tag = "abstract"
        elif tag in {"PY", "Y1"}:
            current["year"] = value
            last_tag = "year"
        elif tag == "DO":
            current["doi"] = value
            last_tag = "doi"
        elif tag == "C5" and value.isdigit():
            current.setdefault("pmid", value)
            last_tag = "pmid"
        elif tag == "AN" and re.fullmatch(r"PMID\s*:?\s*\d+", value, flags=re.I):
            current.setdefault("pmid", re.sub(r"\D", "", value))
            last_tag = "pmid"
        elif tag == "M3":
            current["study_type"] = value
            last_tag = "study_type"
        elif tag == "ER":
            if current:
                records.append(current)
            current = {}
            last_tag = ""
    if current:
        records.append(current)
    return records


def parse_nbib(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_field = ""
    fields = {"TI": "title", "AB": "abstract", "DP": "year"}
    for raw in text.lstrip("\ufeff").splitlines():
        if not raw.strip():
            if current:
                records.append(current)
                current = {}
                last_field = ""
            continue
        match = re.match(r"^([A-Z]{2,4})\s*-\s?(.*)$", raw)
        if match:
            tag, value = match.group(1), match.group(2).strip()
            if tag == "PMID":
                if current:
                    records.append(current)
                current = {"pmid": value}
                last_field = "pmid"
            elif tag in fields:
                current[fields[tag]] = value
                last_field = fields[tag]
            elif tag in {"AID", "LID"} and "[doi]" in value.lower():
                current["doi"] = re.sub(r"\s*\[doi\]\s*$", "", value, flags=re.I).strip()
                last_field = "doi"
            else:
                last_field = ""
        elif raw.startswith(" ") and last_field in {"title", "abstract"}:
            current[last_field] = (current.get(last_field, "") + " " + raw.strip()).strip()
    if current:
        records.append(current)
    return records


def parse_export(path: Path) -> list[dict[str, str]]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    suffix = Path(path).suffix.lower()
    if suffix == ".ris":
        return parse_ris(text)
    if suffix == ".nbib":
        return parse_nbib(text)
    raise ValueError(f"unsupported frozen import format: {suffix}")


def freeze_import(source_path: Path, corpus_path: Path, raw_dir: Path, *, source_name: str) -> dict[str, Any]:
    source_path = Path(source_path)
    source_bytes = source_path.read_bytes()
    source_hash = sha256_bytes(source_bytes)
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_filename = f"{source_hash[:16]}-{source_path.name}"
    frozen_source = raw_dir / raw_filename
    if frozen_source.exists() and sha256_bytes(frozen_source.read_bytes()) != source_hash:
        raise IntegrityError(f"frozen raw path collision: {frozen_source}")
    frozen_source.write_bytes(source_bytes)
    records = []
    for index, raw in enumerate(parse_export(frozen_source)):
        if record := _finish(raw, index, source_name, raw_filename, source_hash):
            records.append(record)
    corpus_body = {
        "version": "frozen-corpus-v1",
        "source_name": source_name,
        "source_file": source_path.name,
        "source_hash": source_hash,
        "records": records,
    }
    corpus = {**corpus_body, "corpus_hash": object_hash(corpus_body)}
    write_json(Path(corpus_path), corpus)
    return corpus
