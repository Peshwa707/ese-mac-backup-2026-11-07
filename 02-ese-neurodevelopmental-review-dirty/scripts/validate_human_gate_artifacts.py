#!/usr/bin/env python3
"""Validate externally supplied human-review artifacts without approving them.

Expected inputs, when available:
  human_review_package/human_adjudication.csv
  human_review_package/clinical_signoff.json

This command is intentionally read-only and fail-closed. It never changes
run_state.json, signs an engine gate, or unlocks conclusions.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def fail(message: str) -> int:
    print(f"BLOCKED: {message}", file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    run_dir = args.run_dir
    package = run_dir / "human_review_package"
    queue_path = package / "human_review_queue.csv"
    adjudication_path = package / "human_adjudication.csv"
    clinical_path = package / "clinical_signoff.json"
    if not queue_path.exists():
        return fail("human_review_queue.csv is missing")
    if not adjudication_path.exists():
        return fail("human_adjudication.csv is missing; the template is not an approval")
    if not clinical_path.exists():
        return fail("clinical_signoff.json is missing; the markdown template is not an approval")

    queue = list(csv.DictReader(queue_path.open(newline="", encoding="utf-8")))
    adjudication = list(csv.DictReader(adjudication_path.open(newline="", encoding="utf-8")))
    if len(queue) != len(adjudication):
        return fail(f"adjudication row count {len(adjudication)} does not match queue {len(queue)}")
    expected = [x.get("pmid", "") for x in queue]
    got = [x.get("pmid", "") for x in adjudication]
    if got != expected:
        return fail("adjudication PMIDs/order do not exactly match the immutable queue")
    allowed = {"include", "exclude", "uncertain"}
    for i, row in enumerate(adjudication, 1):
        decision = (row.get("human_decision") or "").strip().lower()
        if decision not in allowed:
            return fail(f"row {i} has invalid human_decision {decision!r}")
        for key in ("human_rationale", "reviewer_name", "reviewer_qualification", "reviewed_at_utc"):
            if not (row.get(key) or "").strip():
                return fail(f"row {i} is missing {key}")
        if (row.get("source_checked") or "").strip().lower() != "yes":
            return fail(f"row {i} does not confirm source_checked=yes")

    try:
        clinical = json.loads(clinical_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"clinical_signoff.json is not valid JSON: {type(exc).__name__}")
    if str(clinical.get("status", "")).strip().lower() != "approved":
        return fail("clinical sign-off status is not approved")
    for key in ("clinician_name", "credentials", "reviewed_at_utc"):
        if not str(clinical.get(key, "")).strip():
            return fail(f"clinical sign-off is missing {key}")

    print(json.dumps({
        "status": "validated_pending_engine_gate_record",
        "human_records": len(adjudication),
        "clinical_signoff": "approved",
        "note": "Validation does not approve the engine gate or unlock conclusions.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
