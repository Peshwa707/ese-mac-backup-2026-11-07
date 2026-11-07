#!/usr/bin/env python3
"""Repo-scoped Claude/PAI loop for the isolated ESE WebUI queue.

Claude implements one UI stage at a time, this controller runs the local test
 suite and stages only the resulting diff, and the parked Codex reviewer must
 approve the exact Git tree before it is committed. Approval is only honored
 from an authenticated `codex` sender, and only for the exact reviewed tree
 and parent commit. The loop stops at the full offline-stack gate by
writing a filesystem receipt (STOP_RECEIPT), but only after independently
 confirming, from the exact reviewed Git tree object, that a specific final
 full-stack evidence file exists at an exact path and contains an exact
 marker line (see `full_stack_evidence_verified`). Claude's free-form
stdout (the `STAGE:` line it reports) is informational only and is never
trusted to trigger this irreversible hard stop; it never enables live APIs
or loads credentials.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".agenttalk" / "state" / "pai-loop.json"
LOCK = ROOT / ".agenttalk" / "state" / "pai-loop.lock"
TIMEOUT = 3600

# The final stage in docs/UI_BUILD_LOOP.md ("Full offline stack") is the
# offline hard stop. The earlier Compatibility receipt is a milestone, not a
# stop condition. Completion is never taken from Claude's self-reported
# `STAGE:` text; it is recognized only via the final reviewed-tree evidence.
STOP_RECEIPT = ROOT / ".agenttalk" / "state" / "full-stack-offline.receipt.json"

# Exact path and exact marker line required, inside the reviewed Git tree,
# to treat the offline Compatibility stage as genuinely complete. The path is
# relative to the repository root and is read via `git cat-file -p
# <tree>:<path>` against the tree hash that `commit_reviewed` already
# re-verified equals both the live working tree and the tree Codex approved
# -- never from the working directory alone and never from Claude's stdout.
COMPATIBILITY_EVIDENCE_PATH = "docs/reviews/UI_COMPATIBILITY_RECEIPT.md"
COMPATIBILITY_EVIDENCE_MARKER = "UI_COMPATIBILITY_VERIFIED: true"
FULL_STACK_EVIDENCE_PATH = "docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md"
FULL_STACK_EVIDENCE_MARKER = "FULL_STACK_OFFLINE_VERIFIED: true"


def run(args: list[str], *, timeout: int = 120, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, input=input_text, capture_output=True, text=True, timeout=timeout)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def stage_complete() -> bool:
    """Enforceable, filesystem-based hard stop for the full offline stack.

    This checks a receipt file rather than in-memory or JSON-state flags so it
    cannot be bypassed by deleting/editing pai-loop.json or by restarting the
    process; only an explicit human deletion of the receipt file re-opens work.
    """
    return STOP_RECEIPT.exists()


def compatibility_evidence_verified(tree_hash: str) -> bool:
    """Verify the earlier compatibility milestone receipt.

    This milestone is intentionally not sufficient to stop the loop.
    It reads the receipt file at the exact `COMPATIBILITY_EVIDENCE_PATH`
    directly out of the exact reviewed Git tree object (`tree_hash`) using
    `git cat-file`, and requires the file to contain a line that exactly
    equals `COMPATIBILITY_EVIDENCE_MARKER` after stripping surrounding
    whitespace. A missing file, a wrong path, a near-miss marker (e.g. a
    substring match, different casing, or `... : false`), or any other
    content is treated as "not verified".

    Deliberately excluded from this decision: Claude's self-reported
    `STAGE:` line. That text is unauthenticated model stdout and can be
    inaccurate, off-topic, or malformed; it must never be able to trigger
    this irreversible hard stop on its own.
    """
    result = run(["git", "cat-file", "-p", f"{tree_hash}:{COMPATIBILITY_EVIDENCE_PATH}"])
    if result.returncode:
        return False
    lines = {line.strip() for line in result.stdout.splitlines()}
    return COMPATIBILITY_EVIDENCE_MARKER in lines


def full_stack_evidence_verified(tree_hash: str) -> bool:
    """Require the exact final full-stack marker in the reviewed Git tree."""
    result = run(["git", "cat-file", "-p", f"{tree_hash}:{FULL_STACK_EVIDENCE_PATH}"])
    if result.returncode:
        return False
    lines = {line.strip() for line in result.stdout.splitlines()}
    return FULL_STACK_EVIDENCE_MARKER in lines


def extract_stage(notes: str) -> str | None:
    for line in notes.splitlines():
        line = line.strip()
        if line.startswith("STAGE:"):
            return line.split(":", 1)[1].strip() or None
    return None


def write_stop_receipt(commit: str, tree_hash: str, subject: str) -> None:
    STOP_RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    tmp = STOP_RECEIPT.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "stage": "Full offline stack",
                "commit": commit,
                "tree": tree_hash,
                "subject": subject,
                "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(STOP_RECEIPT)


def paths() -> set[str]:
    result = run(["git", "status", "--porcelain", "-z"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return {item[3:] for item in result.stdout.split("\0") if item}


def unstaged() -> set[str]:
    result = run(["git", "diff", "--name-only"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return {line for line in result.stdout.splitlines() if line}


def staged() -> bool:
    return run(["git", "diff", "--cached", "--quiet"]).returncode != 0


def tree() -> str:
    result = run(["git", "write-tree"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git write-tree failed")
    return result.stdout.strip()


def head() -> str:
    result = run(["git", "rev-parse", "HEAD"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "repository has no HEAD")
    return result.stdout.strip()


def claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "sonnet", "--tools", "Read,Edit,Write",
         "--permission-mode", "acceptEdits", "--setting-sources", "", "--system-prompt", "",
         "--output-format", "text", "--no-session-persistence"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Claude exited nonzero")
    return result.stdout.strip()


def tests() -> str:
    # Use module execution so Python places the repository root on sys.path
    # under LaunchAgent execution as well as from an interactive shell.
    result = run([sys.executable, "-m", "pytest", "-q"], timeout=TIMEOUT)
    if result.returncode:
        raise RuntimeError((result.stdout or result.stderr)[-5000:])
    return (result.stdout or "").strip().splitlines()[-1]


def send_review(handoff: str, subject: str) -> str:
    result = run(["agenttalk", "send", "--from", "pai", "--to", "codex", "--kind", "review-request",
                  "--subject", subject, "--file", handoff])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "agenttalk send failed")
    match = re.search(r"request_id:\s*(rq-[A-Za-z0-9]+)", result.stdout)
    if not match:
        raise RuntimeError("review request id missing")
    return match.group(1)


def review_result(request_id: str) -> tuple[str, str] | None:
    for message_path in sorted((ROOT / ".agenttalk" / "messages").glob("*.json"), reverse=True):
        try:
            message = json.loads(message_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (message.get("kind") == "review-result" and message.get("to") == "pai"
                and message.get("from") == "codex"
                and (message.get("meta") or {}).get("request_id") == request_id):
            body = message.get("body", "")
            return body.splitlines()[0].strip(), body
    return None


def handoff(subject: str, test_summary: str, changed: list[str]) -> str:
    directory = ROOT / "docs" / "reviews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"wp-ui-auto-{time.strftime('%Y%m%d-%H%M%S')}-handoff.md"
    stat = run(["git", "diff", "--cached", "--stat"]).stdout.strip()
    path.write_text(
        f"""HANDOFF: {subject}\n\nINTENT\nBounded offline work package selected from docs/UI_BUILD_LOOP.md.\n\nSCOPE\nFiles changed by Claude/PAI: {', '.join(changed)}\n\nTESTS\n`pytest -q` — {test_summary}\n\nDIFF STAT\n```text\n{stat}\n```\n\nHARD STOP\nNo live APIs, credentials, subscriptions, institutional access, or changes to\nthe engine repositories were used. Independent Codex review is required before\ncommit.\n""",
        encoding="utf-8",
    )
    run(["git", "add", "--", str(path.relative_to(ROOT))])
    return str(path.relative_to(ROOT))


def commit_reviewed(state: dict) -> str:
    expected_tree, expected_head = state["tree"], state["head"]
    if tree() != expected_tree or head() != expected_head:
        raise RuntimeError("reviewed Git tree changed; approval invalidated")
    result = run(["git", "commit-tree", expected_tree, "-p", expected_head],
                 input_text=f"{state.get('subject', 'advance WebUI')}\n\nReviewed tree: {expected_tree}\n")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "commit-tree failed")
    ref = run(["git", "symbolic-ref", "-q", "HEAD"]).stdout.strip()
    commit = result.stdout.strip()
    update = run(["git", "update-ref", ref, commit, expected_head])
    if update.returncode:
        raise RuntimeError(update.stderr.strip() or "update-ref failed")
    return commit[:12]


def prompt(review: str | None = None) -> str:
    if review:
        task = f"""The staged WebUI package received this independent review:\n\n{review}\n\nFix only blocking findings, preserve the bounded UI stage, and do not start a later stage. If a finding is incorrect, report `DISPUTE: <reason>` and stop."""
    else:
        task = (
            "Read docs/UI_BUILD_LOOP.md and implement only the first incomplete UI stage. "
            "Work test-first and do not start later stages. Report the exact stage name you "
            "implemented on its own line as `STAGE: <name>`, using one of the stage names from "
            "docs/UI_BUILD_LOOP.md verbatim (for example `STAGE: Compatibility`). This STAGE "
            "line is informational only: the controller never trusts it to stop the loop. "
            "If, and only if, you implement the final 'Full offline stack' stage and its pinned "
            f"engine-artifact contract genuinely passes, also write the exact evidence file "
            f"`{FULL_STACK_EVIDENCE_PATH}` containing a line that reads exactly "
            f"`{FULL_STACK_EVIDENCE_MARKER}`; the independent reviewer must confirm that "
            "file and marker before the controller will ever treat the offline stage as done."
        )
    return f"""You are Claude/PAI implementing one bounded offline WebUI package in {ROOT}.\n\n{task}\n\nHard limits:\n- No live APIs, network, credentials, subscriptions, institutional access, or external communication.\n- Do not inspect or modify /Users/neurologic/Projects/evidence-synthesis-engine or its screen-prove checkout.\n- Do not commit or send agenttalk messages; the local controller runs tests and stages the exact diff.\n- Preserve unrelated untracked files and keep the WebUI separate from engine source.\n\nWhen finished, report changed files and uncertainty briefly, then stop."""


def cycle() -> str:
    if stage_complete():
        return (
            "stopped: full offline stack complete "
            f"(receipt: {STOP_RECEIPT.relative_to(ROOT)}); no further work will be started"
        )
    state = load_state()
    pending = state.get("pending_request_id")
    if pending:
        result = review_result(pending)
        if result is None:
            return f"waiting for Codex review: {pending}"
        verdict, body = result
        state["pending_request_id"] = None
        if verdict == "VERDICT: APPROVE":
            commit = commit_reviewed(state)
            if full_stack_evidence_verified(state["tree"]):
                write_stop_receipt(commit, state["tree"], state.get("subject", ""))
                save_state(state)
                return f"committed approved UI package and reached the full offline stack stop: {commit}"
            save_state(state)
            return f"committed approved UI package: {commit}"
        if verdict != "VERDICT: CHANGES":
            raise RuntimeError(f"unexpected reviewer verdict: {verdict}")
        before, before_unstaged = paths(), unstaged()
        notes = claude(prompt(body))
        if any(line.strip().startswith("DISPUTE:") for line in notes.splitlines()):
            state["awaiting_human"] = True
            state["blocker"] = next(line.strip() for line in notes.splitlines() if line.strip().startswith("DISPUTE:"))
            save_state(state)
            return "waiting for human adjudication of disputed review finding"
        summary = tests()
        changed = sorted((paths() - before) | (unstaged() - before_unstaged))
        if changed:
            run(["git", "add", "--", *changed])
        subject = f"{state.get('subject', 'WebUI offline package')} — re-review"
        handoff_path = handoff(subject, summary, changed)
        state.update(subject=subject, tree=tree(), head=head())
        state["pending_request_id"] = send_review(handoff_path, subject)
        save_state(state)
        return f"fixed and re-handed off: {state['pending_request_id']}"
    if state.get("awaiting_human"):
        return "waiting for human adjudication of disputed review finding"
    if staged():
        return "staged work has no driver-owned review request; waiting safely"
    before, before_unstaged = paths(), unstaged()
    notes = claude(prompt())
    summary = tests()
    changed = sorted((paths() - before) | (unstaged() - before_unstaged))
    if changed:
        run(["git", "add", "--", *changed])
    if not staged():
        raise RuntimeError("Claude produced no staged UI change")
    subject = "WebUI offline queue work package"
    handoff_path = handoff(subject, summary, changed)
    state = {
        "subject": subject,
        "tree": tree(),
        "head": head(),
        "pending_request_id": None,
        "stage": extract_stage(notes),
    }
    state["pending_request_id"] = send_review(handoff_path, subject)
    save_state(state)
    return f"implemented and handed off: {state['pending_request_id']}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        while True:
            try:
                print(cycle(), flush=True)
            except Exception as exc:
                print(f"PAI_LOOP_ERROR: {exc}", file=sys.stderr, flush=True)
                return 1
            if args.once:
                return 0
            time.sleep(max(15, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
