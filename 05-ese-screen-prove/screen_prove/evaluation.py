"""Sealed published-label fixtures and post-run comparison metrics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import IntegrityError, object_hash, read_json, write_json
from .models import ScreenDecision


def seal_labels(labels: dict[str, str], path: Path | None = None) -> dict[str, Any]:
    normalized = {str(key): ScreenDecision(value).value for key, value in sorted(labels.items())}
    body = {"version": "sealed-published-labels-v1", "labels": normalized}
    sealed = {**body, "labels_hash": object_hash(body)}
    if path is not None:
        write_json(Path(path), sealed)
    return sealed


def verify_sealed_labels(sealed: dict[str, Any] | Path) -> dict[str, Any]:
    raw = read_json(sealed) if isinstance(sealed, (str, Path)) else sealed
    if not isinstance(raw, dict):
        raise IntegrityError("invalid sealed published-label artifact")
    body = {"version": raw.get("version"), "labels": raw.get("labels")}
    if raw.get("labels_hash") != object_hash(body):
        raise IntegrityError("labels_hash does not match sealed labels")
    if body["version"] != "sealed-published-labels-v1" or not isinstance(body["labels"], dict):
        raise IntegrityError("invalid sealed published-label artifact")
    try:
        for value in body["labels"].values():
            ScreenDecision(value)
    except (TypeError, ValueError) as exc:
        raise IntegrityError("sealed labels contain an invalid decision") from exc
    return raw


def compare_package(package: dict[str, Any], sealed: dict[str, Any]) -> dict[str, int]:
    labels = sealed["labels"]
    rows = package.get("results", [])
    record_ids = [str(row["record_id"]) for row in rows]
    if len(record_ids) != len(set(record_ids)):
        raise IntegrityError("package contains duplicate record IDs")
    results = {str(row["record_id"]): row["decision"] for row in rows}
    matched = disagreements = ai_abstentions = missing_published = missing_ai = 0
    for record_id, label in labels.items():
        if record_id not in results:
            missing_ai += 1
            continue
        decision = results[record_id]
        if decision == ScreenDecision.ABSTAIN.value:
            ai_abstentions += 1
        elif decision == label:
            matched += 1
        else:
            disagreements += 1
    missing_published = sum(1 for record_id in results if record_id not in labels)
    return {
        "matched": matched,
        "disagreements": disagreements,
        "ai_abstentions": ai_abstentions,
        "missing_published_label": missing_published,
        "missing_ai_result": missing_ai,
        "published_label_count": len(labels),
    }
