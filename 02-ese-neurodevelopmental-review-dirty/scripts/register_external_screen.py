#!/usr/bin/env python3
"""Register a completed external dual-CLI screen as a guarded engine stage.

The CLI screen is deliberately run outside the engine adapter so the Codex and
Claude subscription providers remain auditable.  This script only registers the
already-reconciled artifacts; it never grants the human gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.sr.orchestrator import state as st
from core.sr.orchestrator.hashing import hash_file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    run_dir = args.run_dir
    required = ["screening.json", "screening_r1.json", "screening_r2.json", "reconcile.json"]
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        raise SystemExit("BLOCKED: missing reconciled artifacts: " + ", ".join(missing))
    recon = json.loads((run_dir / "reconcile.json").read_text(encoding="utf-8"))
    # The dual-CLI reconciler stores reviewer identities/states in its
    # ``reviewers`` block (the canonical engine shape uses top-level blocks).
    # Accept either representation, but fail closed unless both explicitly
    # remain pending; this registrar must never grant a human gate.
    human = recon.get("human_reviewer_2") or recon.get("reviewers", {}).get("human_reviewer_2")
    clinical = recon.get("clinical_signoff") or recon.get("reviewers", {}).get("clinical_signoff")
    if not (human == "pending" or (isinstance(human, dict) and human.get("status") == "pending")):
        raise SystemExit("BLOCKED: unexpected human Reviewer 2 state in reconcile.json")
    if not (clinical == "pending" or (isinstance(clinical, dict) and clinical.get("status") == "pending")):
        raise SystemExit("BLOCKED: unexpected clinical sign-off state in reconcile.json")
    state = st.load(run_dir)
    screen = state.stages.get("screen") or st.StageState(name="screen")
    if screen.status not in ("pending", "awaiting_approval"):
        raise SystemExit(f"BLOCKED: screen stage is already {screen.status!r}")
    screen.status = "awaiting_approval"
    screen.output_hashes = {name: hash_file(run_dir / name) for name in required}
    screen.input_hashes = {
        name: hash_file(run_dir / name) for name in ("to_screen.json", "prescreen.json")
        if (run_dir / name).is_file()
    }
    screen.error_code = ""
    screen.gate_record_id = ""
    state.stages["screen"] = screen
    st.save(run_dir, state)
    print(json.dumps({"status": screen.status, "stage": "screen", "human_gate": "pending"}))
    return 0


if __name__ == "__main__":
    main()
