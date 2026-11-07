#!/usr/bin/env python3
"""Small, repo-scoped Claude/PAI driver for the offline build queue.

The driver gives Claude file tools only. It runs tests, stages the exact
package diff, sends the handoff to the parked Codex reviewer, and commits only
after an explicit APPROVE. It stops permanently only after an independently
reviewed offline-engine completion receipt is committed; that receipt never
authorizes live API qualification.
"""

from __future__ import annotations

import argparse
import fcntl
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = REPO_ROOT / ".agenttalk" / "state" / "pai-loop.json"
LOCK_PATH = REPO_ROOT / ".agenttalk" / "state" / "pai-loop.lock"
FINAL_EVIDENCE_PATH = REPO_ROOT / "docs" / "reviews" / "ENGINE_OFFLINE_RECEIPT.md"
FINAL_EVIDENCE_MARKER = "ENGINE_OFFLINE_VERIFIED: true"
STOP_RECEIPT_PATH = REPO_ROOT / ".agenttalk" / "state" / "engine-offline.receipt.json"
HANDOFF_DIR = REPO_ROOT / "docs" / "reviews"
# Validation-matrix work packages may require a longer model design pass.
# Keep the controller bounded, but allow approximately one hour before the
# worker is treated as timed out and safely retried.
CLAUDE_TIMEOUT = 3600


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def _run(
    args: list[str],
    *,
    timeout: int = 120,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd or REPO_ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _status_paths() -> set[str]:
    result = _run(["git", "status", "--porcelain", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    paths: set[str] = set()
    for record in result.stdout.split("\0"):
        if not record:
            continue
        path = record[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return paths


def _has_staged_diff() -> bool:
    return _run(["git", "diff", "--cached", "--quiet"]).returncode != 0


def _staged_tree_id() -> str:
    """Return the exact Git tree represented by the current index."""
    result = _run(["git", "write-tree"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git write-tree failed")
    return result.stdout.strip()


def _head_id() -> str:
    result = _run(["git", "rev-parse", "HEAD"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git rev-parse HEAD failed")
    return result.stdout.strip()


def _record_review_request(state: dict, handoff: str, subject: str) -> str:
    """Capture the reviewed tree before sending the request."""
    tree_id = _staged_tree_id()
    parent_id = _head_id()
    request_id = _send_review(handoff, subject)
    state["reviewed_tree_id"] = tree_id
    state["reviewed_parent_id"] = parent_id
    state["pending_request_id"] = request_id
    return request_id


def _commit_reviewed_tree(tree_id: str, parent_id: str, subject: str) -> str:
    """Create a commit from the reviewed tree, never by rereading the index."""
    head = _head_id()
    if head != parent_id:
        raise RuntimeError("HEAD changed after review; approval is invalid")
    commit = _run(
        ["git", "commit-tree", tree_id, "-p", parent_id],
        input_text=f"{subject}\n\nReviewed tree: {tree_id}\n",
    )
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or "git commit-tree failed")
    ref = _run(["git", "symbolic-ref", "-q", "HEAD"])
    if ref.returncode != 0:
        raise RuntimeError(ref.stderr.strip() or "detached HEAD is not supported")
    update = _run(["git", "update-ref", ref.stdout.strip(), commit.stdout.strip(), parent_id])
    if update.returncode != 0:
        raise RuntimeError(update.stderr.strip() or "git update-ref failed")
    return commit.stdout.strip()


def _unstaged_paths() -> set[str]:
    result = _run(["git", "diff", "--name-only"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return {line for line in result.stdout.splitlines() if line}


def _ready() -> bool:
    """Stop only after a validated receipt matches the current reviewed HEAD."""
    if not STOP_RECEIPT_PATH.is_file():
        return False
    try:
        receipt = json.loads(STOP_RECEIPT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    commit_id = receipt.get("commit")
    tree_id = receipt.get("tree")
    if (
        not isinstance(commit_id, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit_id)
        or not isinstance(tree_id, str)
        or not re.fullmatch(r"[0-9a-f]{40}", tree_id)
    ):
        return False
    if _head_id() != commit_id:
        return False
    commit_tree = _run(["git", "show", "-s", "--format=%T", commit_id])
    if commit_tree.returncode != 0 or commit_tree.stdout.strip() != tree_id:
        return False
    return _final_evidence_verified(tree_id)


@functools.lru_cache(maxsize=8)
def _independently_verified_receipt(commit_id: str) -> tuple[bool, str, str, str]:
    """Recompute the offline completion receipt from a clean, detached
    worktree checked out at `commit_id`, independent of anything an evidence
    file merely claims.

    A hand-authored or otherwise fabricated `ENGINE_OFFLINE_RECEIPT.md` marker
    must never be trusted on its own: this re-derives `passed`, the resolved
    `git_head` commit/tree, and `engine_offline_hash` by actually invoking
    `screen_prove.cli offline-complete` (which runs the full offline
    doctor/qualification/validation/contract/test suite) against the real
    commit contents. Returns `(ok, commit, tree, engine_offline_hash)`; `ok`
    is False if the worktree or command could not be produced and read, or if
    the recomputed receipt did not genuinely pass.
    """
    with tempfile.TemporaryDirectory(prefix="screen-prove-offline-verify-") as temp_root:
        added = _run(
            ["git", "worktree", "add", "--detach", temp_root, commit_id],
            timeout=CLAUDE_TIMEOUT,
        )
        if added.returncode != 0:
            return (False, "", "", "")
        try:
            output_path = Path(temp_root) / "engine-offline-receipt.verify.json"
            _run(
                [
                    sys.executable,
                    "-m",
                    "screen_prove.cli",
                    "offline-complete",
                    "--repo-root",
                    temp_root,
                    "--output",
                    str(output_path),
                ],
                timeout=CLAUDE_TIMEOUT,
                cwd=Path(temp_root),
            )
            if not output_path.is_file():
                return (False, "", "", "")
            try:
                receipt = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return (False, "", "", "")
        finally:
            _run(["git", "worktree", "remove", "--force", temp_root], timeout=120)
    if not isinstance(receipt, dict) or receipt.get("passed") is not True:
        return (False, "", "", "")
    git_head = receipt.get("git_head")
    if not isinstance(git_head, dict):
        return (False, "", "", "")
    return (
        True,
        str(git_head.get("commit", "")),
        str(git_head.get("tree", "")),
        str(receipt.get("engine_offline_hash", "")),
    )


def _final_evidence_verified(tree_id: str) -> bool:
    """Validate final evidence provenance from the exact reviewed Git tree."""
    relative_path = FINAL_EVIDENCE_PATH.relative_to(REPO_ROOT)
    result = _run(["git", "cat-file", "-p", f"{tree_id}:{relative_path}"])
    if result.returncode != 0:
        return False
    lines = {line.strip() for line in result.stdout.splitlines()}
    if FINAL_EVIDENCE_MARKER not in lines:
        return False

    def value(prefix: str) -> str:
        for line in lines:
            if line.startswith(prefix):
                return line[len(prefix):].strip().strip("`")
        return ""

    source_commit = value("- Commit:")
    source_tree = value("- Tree:")
    receipt_hash = value("- Engine receipt hash:")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", source_commit)
        or not re.fullmatch(r"[0-9a-f]{40}", source_tree)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt_hash)
    ):
        return False

    current_head = _head_id()
    parent = _run(["git", "show", "-s", "--format=%P", current_head])
    source_tree_result = _run(["git", "show", "-s", "--format=%T", source_commit])
    if parent.returncode != 0 or source_tree_result.returncode != 0:
        return False
    parents = parent.stdout.split()
    if (
        len(parents) != 1
        or parents[0] != source_commit
        or source_tree_result.stdout.strip() != source_tree
    ):
        return False

    # The checks above only confirm the evidence file is internally
    # consistent with real git history -- a fabricated file can trivially
    # quote its own immediate parent commit/tree, since those already exist.
    # Independently re-derive the receipt from a clean worktree at that exact
    # commit and require its genuine `passed` result, resolved git_head, and
    # content hash to match what the evidence file claims before trusting the
    # marker.
    ok, verified_commit, verified_tree, verified_hash = _independently_verified_receipt(
        source_commit
    )
    return (
        ok
        and verified_commit == source_commit
        and verified_tree == source_tree
        and verified_hash == receipt_hash
    )


def _final_stage_tree(tree_id: str) -> bool:
    """Identify the final engine package from its reviewed tree contents."""
    result = _run(["git", "cat-file", "-e", f"{tree_id}:screen_prove/engine_offline.py"])
    return result.returncode == 0


def _materialize_final_evidence(commit_id: str) -> None:
    """Build final evidence from a clean worktree for the approved commit."""
    relative_path = FINAL_EVIDENCE_PATH.relative_to(REPO_ROOT)
    with tempfile.TemporaryDirectory(prefix="screen-prove-offline-evidence-") as temp_root:
        added = _run(
            ["git", "worktree", "add", "--detach", temp_root, commit_id],
            timeout=CLAUDE_TIMEOUT,
        )
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or "unable to create clean evidence worktree")
        try:
            evidence_path = Path(temp_root) / relative_path
            result = _run(
                [
                    sys.executable,
                    "-m",
                    "screen_prove.cli",
                    "offline-complete",
                    "--repo-root",
                    temp_root,
                    "--evidence",
                    str(evidence_path),
                ],
                timeout=CLAUDE_TIMEOUT,
                cwd=Path(temp_root),
            )
            if result.returncode != 0:
                detail = (result.stdout or result.stderr).strip()
                raise RuntimeError(detail[-5000:] or "offline completion evidence failed")
            if not evidence_path.is_file():
                raise RuntimeError("offline completion did not create the final evidence file")
            target = REPO_ROOT / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(evidence_path.read_bytes())
        finally:
            removed = _run(
                ["git", "worktree", "remove", "--force", temp_root],
                timeout=120,
            )
            if removed.returncode != 0:
                raise RuntimeError(removed.stderr.strip() or "unable to remove evidence worktree")


def _write_stop_receipt(commit_id: str, tree_id: str, subject: str) -> None:
    STOP_RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STOP_RECEIPT_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "stage": "Engine offline completion",
                "commit": commit_id,
                "tree": tree_id,
                "subject": subject,
                "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(STOP_RECEIPT_PATH)


def _recover_completion_after_commit(state: dict) -> bool:
    """Recover a receipt if the process died after approved commit installation."""
    request_id = state.get("pending_request_id")
    expected_tree = state.get("reviewed_tree_id")
    expected_parent = state.get("reviewed_parent_id")
    if not request_id or not isinstance(expected_tree, str) or not isinstance(expected_parent, str):
        return False
    result = _review_result(request_id)
    if not result or result[0] != "VERDICT: APPROVE":
        return False
    current_head = _head_id()
    current_tree_result = _run(["git", "show", "-s", "--format=%T", current_head])
    current_parent_result = _run(["git", "show", "-s", "--format=%P", current_head])
    if current_tree_result.returncode != 0 or current_parent_result.returncode != 0:
        return False
    if current_tree_result.stdout.strip() != expected_tree:
        return False
    if current_parent_result.stdout.strip() != expected_parent:
        return False
    if not _final_evidence_verified(expected_tree):
        return False
    _write_stop_receipt(current_head, expected_tree, state.get("subject", ""))
    state["pending_request_id"] = None
    state["last_verdict"] = "VERDICT: APPROVE"
    _save_state(state)
    return True


def _claude_prompt(*, fixing: str | None = None, committing: bool = False) -> str:
    if committing:
        return f"""You are Claude/PAI coordinating the isolated offline build at {REPO_ROOT}.

The parked Codex reviewer returned VERDICT: APPROVE for the currently staged
work package. Commit only that staged package with a concise message. Do not
edit files, do not amend older commits, do not push, do not use network or
credentials, and do not start the next work package in this turn.
"""
    if fixing:
        task = f"""The current staged work package received this independent review:

{fixing}

Fix only the blocking findings, preserve the bounded scope, and do not start
another queue item. If a finding is genuinely incorrect or cannot be resolved
without changing the intended contract, do not make a speculative edit. Report
`DISPUTE: <reason>` as a standalone line with concise evidence and stop so the
human can adjudicate it."""
    else:
        task = f"""Read docs/AUTONOMOUS_BUILD_LOOP.md and implement only the first
incomplete queue item. Work test-first and keep the change small. Do not start
later queue items. If, and only if, you implement the final Engine offline
completion stage and its current-head evidence genuinely passes, create
`{FINAL_EVIDENCE_PATH.relative_to(REPO_ROOT)}` containing a line that reads
exactly `{FINAL_EVIDENCE_MARKER}`. The controller will verify that marker from
the independently reviewed Git tree before it writes its stop receipt."""
    return f"""You are Claude/PAI implementing one bounded work package in the isolated repository:
{REPO_ROOT}

{task}

Hard limits:
- Offline only: no network, live APIs, credentials, subscriptions, institutional
  access, external communication, or database calls.
- Do not inspect or modify /Users/neurologic/Projects/evidence-synthesis-engine.
- Use only the file tools available in this invocation; do not send agenttalk
  messages and do not commit. The local controller runs tests, stages the exact
  diff, and performs the review handoff.
- Preserve existing unrelated untracked files. Do not broaden the product.
- If the work is already complete, make no speculative changes and explain why.

When finished, briefly report files changed and any uncertainty. Stop.
"""


def _run_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    result = subprocess.run(
        [
            "claude", "-p", prompt,
            "--model", "sonnet",
            "--tools", "Read,Edit,Write",
            "--permission-mode", "acceptEdits",
            "--setting-sources", "",
            "--system-prompt", "",
            "--output-format", "text",
            "--no-session-persistence",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Claude exited nonzero")
    return result.stdout.strip()


def _run_tests() -> str:
    pytest = "/opt/homebrew/Caskroom/miniconda/base/bin/pytest"
    result = _run([pytest, "-q"], timeout=CLAUDE_TIMEOUT)
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output[-4000:] or result.stderr.strip() or "tests failed")
    return output.splitlines()[-1] if output else "pytest exited 0"


def _stage_new_changes(before: set[str], before_unstaged: set[str]) -> list[str]:
    after = _status_paths()
    after_unstaged = _unstaged_paths()
    changed = sorted((after - before) | (after_unstaged - before_unstaged))
    to_stage = sorted(set(changed))
    if to_stage:
        _run(["git", "add", "--", *to_stage])
    if not _has_staged_diff():
        raise RuntimeError("Claude produced no staged project change")
    return to_stage


def _write_and_stage_handoff(subject: str, test_summary: str, files: list[str]) -> str:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    handoff = HANDOFF_DIR / f"wp-auto-{stamp}-handoff.md"
    stat = _run(["git", "diff", "--cached", "--stat"]).stdout.strip()
    handoff.write_text(
        f"""HANDOFF: {subject}

INTENT
Bounded offline work package selected from docs/AUTONOMOUS_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: {', '.join(files)}

TESTS
`pytest -q` — {test_summary}

DIFF STAT
```text
{stat}
```

HARD STOP
No live APIs, network calls, credentials, subscriptions, institutional access,
or external databases were used. Independent Codex review is required before
commit. The loop stops only at the reviewed ENGINE_OFFLINE_RECEIPT.md gate.
""",
        encoding="utf-8",
    )
    _run(["git", "add", "--", str(handoff.relative_to(REPO_ROOT))])
    return str(handoff.relative_to(REPO_ROOT))


def _send_review(handoff: str, subject: str) -> str:
    result = _run(
        [
            "agenttalk", "send", "--from", "pai", "--to", "codex",
            "--kind", "review-request", "--subject", subject,
            "--file", handoff,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "agenttalk send failed")
    match = re.search(r"request_id:\s*(rq-[A-Za-z0-9]+)", result.stdout)
    if not match:
        raise RuntimeError("agenttalk send did not return a request id")
    return match.group(1)


def _review_result(request_id: str) -> tuple[str, str] | None:
    messages = sorted((REPO_ROOT / ".agenttalk" / "messages").glob("*.json"))
    for path in reversed(messages):
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            message.get("kind") == "review-result"
            and message.get("to") == "pai"
            and (message.get("meta") or {}).get("request_id") == request_id
        ):
            body = message.get("body", "")
            first = body.splitlines()[0].strip() if body else ""
            return first, body
    return None


def run_once(*, dry_run: bool = False) -> str:
    state = _load_state()
    if _recover_completion_after_commit(state):
        return "recovered approved offline completion stop receipt"
    if _ready():
        return "ENGINE_OFFLINE_VERIFIED=true; loop stopped before live APIs"

    if state.get("awaiting_human"):
        return "waiting for human adjudication of disputed review finding"
    pending = state.get("pending_request_id")
    if pending:
        result = _review_result(pending)
        if result is None:
            return f"waiting for Codex review: {pending}"
        verdict, body = result
        _run(["agenttalk", "drain", "--for", "pai"])
        state["pending_request_id"] = None
        state["last_verdict"] = verdict
        if verdict == "VERDICT: APPROVE":
            expected_tree = state.get("reviewed_tree_id")
            expected_parent = state.get("reviewed_parent_id")
            actual_tree = _staged_tree_id()
            actual_parent = _head_id()
            if not expected_tree or not expected_parent or actual_tree != expected_tree or actual_parent != expected_parent:
                state["last_verdict"] = "VERDICT: INVALIDATED"
                state["last_error"] = (
                    "reviewed Git tree or parent changed; approval cannot be used"
                )
                summary = _run_tests()
                files = sorted(_status_paths())
                handoff = _write_and_stage_handoff(
                    "staged diff changed after review — fresh review required",
                    summary,
                    files,
                )
                subject = "staged diff changed after review — fresh review"
                request_id = _record_review_request(state, handoff, subject)
                state["subject"] = subject
                _save_state(state)
                return f"approval invalidated; fresh review requested: {request_id}"
            if dry_run:
                return "would commit approved staged work"
            commit = _commit_reviewed_tree(
                expected_tree,
                expected_parent,
                state.get("subject", "feat: advance offline build"),
            )
            if _final_stage_tree(expected_tree) and not _final_evidence_verified(expected_tree):
                _materialize_final_evidence(commit)
                stage_evidence = _run(
                    ["git", "add", "--", str(FINAL_EVIDENCE_PATH.relative_to(REPO_ROOT))]
                )
                if stage_evidence.returncode != 0:
                    raise RuntimeError(
                        stage_evidence.stderr.strip() or "unable to stage final evidence receipt"
                    )
                summary = _run_tests()
                handoff = _write_and_stage_handoff(
                    "engine offline evidence receipt",
                    summary,
                    [str(FINAL_EVIDENCE_PATH.relative_to(REPO_ROOT))],
                )
                subject = "engine offline evidence receipt"
                state["subject"] = subject
                request_id = _record_review_request(state, handoff, subject)
                _save_state(state)
                return f"committed engine package; evidence receipt re-handed off: {request_id}"
            if _final_evidence_verified(expected_tree):
                _write_stop_receipt(commit, expected_tree, state.get("subject", ""))
            _save_state(state)
            return f"committed approved work package: {commit[:12]}"
        if verdict == "VERDICT: CHANGES":
            rounds = int(state.get("review_rounds", state.get("subject", "").count("re-review"))) + 1
            state["review_rounds"] = rounds
            if dry_run:
                return "would ask Claude to address Codex findings"
            before = _status_paths()
            before_unstaged = _unstaged_paths()
            notes = _run_claude(_claude_prompt(fixing=body))
            dispute = next(
                (line.strip() for line in notes.splitlines() if line.strip().startswith("DISPUTE:")),
                None,
            )
            if dispute:
                state["awaiting_human"] = True
                state["blocker"] = dispute
                state["last_notes"] = notes[-2000:]
                _save_state(state)
                return "ESCALATE: Claude disputed a review finding; human adjudication required"
            summary = _run_tests()
            files = _stage_new_changes(before, before_unstaged)
            handoff = _write_and_stage_handoff(state.get("subject", "offline work package"), summary, files)
            subject = f"{state.get('subject', 'offline work package')} — re-review"
            state["subject"] = subject
            _record_review_request(state, handoff, subject)
            _save_state(state)
            return f"fixed and re-handed off: {state['pending_request_id']}"
        raise RuntimeError(f"unrecognized review verdict: {verdict}")

    if _has_staged_diff():
        return "staged work has no driver-owned review request; waiting safely"

    if dry_run:
        return "would invoke Claude for the first incomplete offline queue item"

    before = _status_paths()
    before_unstaged = _unstaged_paths()
    notes = _run_claude(_claude_prompt())
    summary = _run_tests()
    files = _stage_new_changes(before, before_unstaged)
    subject = "offline queue work package"
    handoff = _write_and_stage_handoff(subject, summary, files)
    state = {
        "pending_request_id": None,
        "subject": subject,
        "review_rounds": 0,
        "last_notes": notes[-2000:],
    }
    request_id = _record_review_request(state, handoff, subject)
    state["last_notes"] = notes[-2000:]
    _save_state({
        **state,
    })
    return f"implemented and handed off: {request_id}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one bounded cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="inspect the next action without invoking Claude")
    parser.add_argument("--interval", type=int, default=60, help="seconds between cycles")
    args = parser.parse_args()
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        while True:
            try:
                print(run_once(dry_run=args.dry_run), flush=True)
            except Exception as exc:  # keep launchd alive and fail closed
                print(f"PAI_LOOP_ERROR: {exc}", file=sys.stderr, flush=True)
                return 1
            if args.once or args.dry_run:
                return 0
            time.sleep(max(15, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
