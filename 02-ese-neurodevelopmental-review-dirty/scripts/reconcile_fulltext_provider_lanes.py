"""Fail-closed reconciliation for independent Codex and Claude full-text lanes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_status(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[int(row["batch"])] = row
    return out


def load_batch(path: Path, expected: list[str]) -> dict[str, dict]:
    data = json.loads(path.read_text())
    got = {str(x["pmid"]): x for x in data}
    if set(got) != set(expected) or len(got) != len(expected):
        raise RuntimeError(f"{path} does not exactly cover its expected PMIDs")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--batch-size", type=int, default=2)
    args = ap.parse_args()
    run_dir = args.run_dir
    out = run_dir / "ai_fulltext_draft"
    records = [x for x in json.loads((out / "fulltext_records.json").read_text()) if x.get("available")]
    ids = [str(x["pmid"]) for x in records]
    batches = [ids[i:i + args.batch_size] for i in range(0, len(ids), args.batch_size)]
    statuses = {p: load_status(out / f"fulltext_cli_{p}_status.jsonl") for p in ("codex", "claude")}
    missing = {
        p: [i for i in range(len(batches)) if statuses[p].get(i, {}).get("status") != "complete"]
        for p in statuses
    }
    if any(missing.values()):
        print(json.dumps({"status": "blocked", "missing": missing}, indent=2))
        return 2

    by_provider = {p: {} for p in statuses}
    for i, expected in enumerate(batches):
        for p in statuses:
            path = out / p / f"batch_{i:03d}.json"
            if not path.exists():
                raise SystemExit(f"BLOCKED: missing parsed {p} output {path}")
            by_provider[p].update(load_batch(path, expected))

    r1 = [by_provider["codex"][pmid] for pmid in ids]
    r2 = [by_provider["claude"][pmid] for pmid in ids]
    consensus = []
    conflicts = []
    for a, b in zip(r1, r2):
        if a["decision"] == b["decision"]:
            item = dict(a)
            item["reason"] = "Codex and Claude agree; draft pending human review."
            item["limitations"] = sorted(set((a.get("limitations") or []) + (b.get("limitations") or [])))
        else:
            conflicts.append(a["pmid"])
            item = dict(a)
            item.update({"decision": "uncertain", "reason": f"Codex/Claude conflict ({a['decision']}/{b['decision']}); human adjudication required.", "supporting_quote": "", "limitations": sorted(set((a.get("limitations") or []) + (b.get("limitations") or []) + ["AI reviewer conflict"]))})
        consensus.append(item)

    (out / "fulltext_codex.json").write_text(json.dumps(r1, indent=2), encoding="utf-8")
    (out / "fulltext_claude.json").write_text(json.dumps(r2, indent=2), encoding="utf-8")
    (out / "fulltext_consensus.json").write_text(json.dumps(consensus, indent=2), encoding="utf-8")
    recon = {"status": "draft_pending_human_review", "records": len(ids), "conflicts": len(conflicts), "conflict_pmids": conflicts, "reviewers": {"ai_reviewer_1": "codex_cli_subscription", "ai_reviewer_2": "claude_cli_subscription", "human_reviewer_2": "pending", "clinical_signoff": "pending"}, "note": "AI-to-AI full-text consensus is not human adjudication and does not unlock clinical conclusions."}
    (out / "fulltext_reconcile.json").write_text(json.dumps(recon, indent=2), encoding="utf-8")
    print(json.dumps(recon, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
