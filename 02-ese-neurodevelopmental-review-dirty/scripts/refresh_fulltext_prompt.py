"""Refresh one persisted full-text prompt after deterministic normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.sr.config import load_sr_config
from run_dual_cli_fulltext_draft import prompt_for


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()

    out = args.run_dir / "ai_fulltext_draft"
    records = [x for x in json.loads((out / "fulltext_records.json").read_text()) if x.get("available")]
    batch = records[args.batch * args.batch_size:(args.batch + 1) * args.batch_size]
    state = json.loads((args.run_dir / "run_state.json").read_text())["state"]
    prompt = prompt_for(batch, load_sr_config(state["vertical"]))
    path = out / "prompts" / f"batch_{args.batch:03d}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(prompt, encoding="utf-8")
    print(json.dumps({"batch": args.batch, "path": str(path), "chars": len(prompt)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
