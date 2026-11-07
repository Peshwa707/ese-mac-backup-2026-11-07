"""Run one independent Codex or Claude full-text lane.

The two lanes intentionally have separate status logs and never wait on one
another. Reconciliation is a later, fail-closed step.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_dual_cli_fulltext_draft import (  # noqa: E402
    FullTextReviewBatch,
    load_sr_config,
    prompt_for,
    run_cli,
    strict_schema,
    validate,
)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def main_async(args) -> int:
    run_dir = Path(args.run_dir)
    out = run_dir / "ai_fulltext_draft"
    records_path = out / "fulltext_records.json"
    if not records_path.exists():
        raise SystemExit("BLOCKED: fulltext_records.json is missing; run legal retrieval first")
    records = [x for x in json.loads(records_path.read_text()) if x.get("available")]
    state = json.loads((run_dir / "run_state.json").read_text())["state"]
    cfg = load_sr_config(state["vertical"])
    schema_path = out / "fulltext_cli_schema.json"
    schema_path.write_text(json.dumps(strict_schema(FullTextReviewBatch.model_json_schema()), indent=2), encoding="utf-8")
    batches = [records[i:i + args.batch_size] for i in range(0, len(records), args.batch_size)]
    status_path = out / f"fulltext_cli_{args.provider}_status.jsonl"
    complete: set[int] = set()
    if status_path.exists():
        for line in status_path.read_text().splitlines():
            try:
                row = json.loads(line)
                if row.get("status") == "complete":
                    complete.add(int(row["batch"]))
            except (ValueError, KeyError):
                pass
    provider_dir = out / args.provider
    provider_dir.mkdir(exist_ok=True)
    for i, batch in enumerate(batches):
        if i in complete and not args.rerun:
            print(f"SKIP {args.provider} batch {i:03d}", flush=True)
            continue
        prompt = prompt_for(batch, cfg)
        (out / "prompts").mkdir(exist_ok=True)
        prompt_path = out / "prompts" / f"batch_{i:03d}.md"
        # Always refresh the persisted prompt. A prompt-side normalization or
        # other deterministic repair must be reflected on disk before reruns;
        # leaving a stale prompt here obscures the exact payload served.
        prompt_path.write_text(prompt, encoding="utf-8")
        raw = provider_dir / f"batch_{i:03d}.raw"
        parsed = provider_dir / f"batch_{i:03d}.json"
        row = {"batch": i, "provider": f"{args.provider}_cli_subscription", "pmids": [x["pmid"] for x in batch], "started_at": now()}
        try:
            rc, stderr = await asyncio.to_thread(run_cli, args.provider, prompt, schema_path, raw, args.model, args.timeout)
            if rc != 0:
                raise RuntimeError(f"CLI returned exit code {rc}: {stderr[-1000:]}")
            parsed.write_text(json.dumps(validate(raw.read_text(encoding="utf-8", errors="replace"), row["pmids"]), indent=2), encoding="utf-8")
            row.update({"status": "complete", "raw": str(raw), "parsed": str(parsed), "completed_at": now()})
        except Exception as exc:
            row.update({"status": "failed", "errors": [f"{type(exc).__name__}: {exc}"], "completed_at": now()})
            with status_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            print(f"STOP {args.provider} batch {i:03d}: failure preserved", file=sys.stderr)
            return 2
        with status_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        print(json.dumps({"provider": args.provider, "batch": i, "status": "complete"}), flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--provider", choices=("codex", "claude"), required=True)
    p.add_argument("--batch-size", type=int, default=2)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--rerun", action="store_true")
    p.add_argument("--model", default="")
    args = p.parse_args()
    if not args.model and args.provider == "claude":
        args.model = os.environ.get("CLAUDE_REVIEW_MODEL", "opus")
    if not args.model and args.provider == "codex":
        args.model = os.environ.get("CODEX_REVIEW_MODEL", "")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
