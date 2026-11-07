"""Wait for both independent full-text lanes, then reconcile once.

This watcher is deliberately fail-closed: provider failures stop the watcher
and remain visible in the provider status ledgers.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def latest_by_batch(path: Path) -> dict[int, dict]:
    """Use the latest ledger row so a successful retry supersedes failure."""
    return {int(row["batch"]): row for row in rows(path) if "batch" in row}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    out = run_dir / "ai_fulltext_draft"
    repo = Path(__file__).resolve().parents[1]
    reconcile = repo / "scripts" / "reconcile_fulltext_provider_lanes.py"
    while True:
        failed: list[tuple[str, int]] = []
        complete: dict[str, set[int]] = {}
        for provider in ("codex", "claude"):
            provider_rows = latest_by_batch(out / f"fulltext_cli_{provider}_status.jsonl")
            failed.extend((provider, batch) for batch, row in provider_rows.items() if row.get("status") == "failed")
            complete[provider] = {batch for batch, row in provider_rows.items() if row.get("status") == "complete"}
        if failed:
            print(json.dumps({"status": "blocked", "provider_failures": failed}), flush=True)
            return 2
        print(json.dumps({"status": "waiting", "codex": len(complete.get("codex", set())), "claude": len(complete.get("claude", set()))}), flush=True)
        proc = subprocess.run([sys.executable, str(reconcile), str(run_dir)], cwd=repo)
        if proc.returncode == 0:
            return 0
        if proc.returncode != 2:
            return proc.returncode
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
