import json
from pathlib import Path

from tools import pai_loop


def test_ready_requires_valid_controller_offline_stop_receipt(monkeypatch, tmp_path):
    stop_receipt = tmp_path / "engine-offline.receipt.json"
    monkeypatch.setattr(pai_loop, "STOP_RECEIPT_PATH", stop_receipt)

    # The legacy pre-live receipt is deliberately not sufficient.
    assert pai_loop._ready() is False
    stop_receipt.write_text(
        '{"commit": "cccccccccccccccccccccccccccccccccccccccc", '
        '"tree": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}\n',
        encoding="utf-8",
    )
    assert pai_loop._ready() is False

    monkeypatch.setattr(pai_loop, "_head_id", lambda: "c" * 40)

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "show", "-s"] and args[-2] == "--format=%T" and args[-1] == "c" * 40:
            return type("Result", (), {"returncode": 0, "stdout": "b" * 40 + "\n", "stderr": ""})()
        if args[:3] == ["git", "show", "-s"] and args[-2] == "--format=%P" and args[-1] == "c" * 40:
            return type("Result", (), {"returncode": 0, "stdout": "a" * 40 + "\n", "stderr": ""})()
        if args[:3] == ["git", "show", "-s"] and args[-2] == "--format=%T" and args[-1] == "a" * 40:
            return type("Result", (), {"returncode": 0, "stdout": "b" * 40 + "\n", "stderr": ""})()
        if args[:3] == ["git", "cat-file", "-p"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": (
                        "ENGINE_OFFLINE_VERIFIED: true\n"
                        "- Commit: `" + "a" * 40 + "`\n"
                        "- Tree: `" + "b" * 40 + "`\n"
                        "- Engine receipt hash: `" + "d" * 64 + "`\n"
                    ),
                    "stderr": "",
                },
            )()
        raise AssertionError(args)

    monkeypatch.setattr(pai_loop, "_run", fake_run)
    monkeypatch.setattr(
        pai_loop,
        "_independently_verified_receipt",
        lambda commit: (True, "a" * 40, "b" * 40, "d" * 64),
    )
    assert pai_loop._ready() is True


def test_ready_rejects_receipt_for_a_different_head(monkeypatch, tmp_path):
    stop_receipt = tmp_path / "engine-offline.receipt.json"
    monkeypatch.setattr(pai_loop, "STOP_RECEIPT_PATH", stop_receipt)
    stop_receipt.write_text(json.dumps({"commit": "a" * 40, "tree": "b" * 40}) + "\n", encoding="utf-8")
    monkeypatch.setattr(pai_loop, "_head_id", lambda: "c" * 40)
    assert pai_loop._ready() is False


def test_recover_completion_after_commit_reconstructs_missing_stop_receipt(monkeypatch):
    saved = []
    state = {
        "pending_request_id": "rq-approved",
        "reviewed_tree_id": "b" * 40,
        "reviewed_parent_id": "a" * 40,
        "subject": "Engine offline completion",
    }

    monkeypatch.setattr(pai_loop, "_review_result", lambda request_id: ("VERDICT: APPROVE", "VERDICT: APPROVE"))
    monkeypatch.setattr(pai_loop, "_head_id", lambda: "c" * 40)

    def fake_run(args, **kwargs):
        if args[3] == "--format=%T":
            stdout = "b" * 40 + "\n"
        else:
            stdout = "a" * 40 + "\n"
        return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(pai_loop, "_run", fake_run)
    monkeypatch.setattr(pai_loop, "_final_evidence_verified", lambda tree_id: tree_id == "b" * 40)
    written = []
    monkeypatch.setattr(
        pai_loop,
        "_write_stop_receipt",
        lambda commit, tree, subject: written.append((commit, tree, subject)),
    )
    monkeypatch.setattr(pai_loop, "_save_state", lambda value: saved.append(dict(value)))

    assert pai_loop._recover_completion_after_commit(state) is True
    assert written == [("c" * 40, "b" * 40, "Engine offline completion")]
    assert saved[-1]["pending_request_id"] is None


def test_final_evidence_verification_requires_bound_provenance(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "cat-file", "-p"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": (
                        "ENGINE_OFFLINE_VERIFIED: true\n"
                        "- Commit: `" + "a" * 40 + "`\n"
                        "- Tree: `" + "b" * 40 + "`\n"
                        "- Engine receipt hash: `" + "d" * 64 + "`\n"
                    ),
                    "stderr": "",
                },
            )()
        if args[-2:] == ["--format=%P", "c" * 40]:
            return type("Result", (), {"returncode": 0, "stdout": "a" * 40 + "\n", "stderr": ""})()
        if args[-2:] == ["--format=%T", "a" * 40]:
            return type("Result", (), {"returncode": 0, "stdout": "b" * 40 + "\n", "stderr": ""})()
        raise AssertionError(args)

    monkeypatch.setattr(pai_loop, "_run", fake_run)
    monkeypatch.setattr(pai_loop, "_head_id", lambda: "c" * 40)
    monkeypatch.setattr(
        pai_loop,
        "_independently_verified_receipt",
        lambda commit: (True, "a" * 40, "b" * 40, "d" * 64),
    )
    assert pai_loop._final_evidence_verified("tree-1") is True
    assert calls[0][:3] == ["git", "cat-file", "-p"]


def test_final_evidence_verification_rejects_unindependently_verified_receipt(monkeypatch):
    monkeypatch.setattr(
        pai_loop,
        "_run",
        lambda args, **kwargs: type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    "ENGINE_OFFLINE_VERIFIED: true\n"
                    "- Commit: `" + "a" * 40 + "`\n"
                    "- Tree: `" + "b" * 40 + "`\n"
                    "- Engine receipt hash: `" + "d" * 64 + "`\n"
                )
                if args[:3] == ["git", "cat-file", "-p"]
                else ("a" * 40 + "\n" if args[-2] == "--format=%P" else "b" * 40 + "\n"),
                "stderr": "",
            },
        )(),
    )
    monkeypatch.setattr(pai_loop, "_head_id", lambda: "c" * 40)
    monkeypatch.setattr(pai_loop, "_independently_verified_receipt", lambda commit: (False, "", "", ""))

    assert pai_loop._final_evidence_verified("tree-1") is False


def test_final_evidence_materialization_runs_cli_from_approved_worktree(monkeypatch, tmp_path):
    clean_root = tmp_path / "clean-worktree"
    clean_root.mkdir()
    primary_root = tmp_path / "primary"
    primary_root.mkdir()
    monkeypatch.setattr(pai_loop, "REPO_ROOT", primary_root)
    monkeypatch.setattr(
        pai_loop,
        "FINAL_EVIDENCE_PATH",
        primary_root / "docs" / "reviews" / "ENGINE_OFFLINE_RECEIPT.md",
    )
    calls = []

    class _FixedTempDir:
        def __enter__(self):
            return str(clean_root)

        def __exit__(self, *exc):
            return False

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:4] == ["git", "worktree", "add", "--detach"]:
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if args[:3] == ["git", "worktree", "remove"]:
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if args[1:4] == ["-m", "screen_prove.cli", "offline-complete"]:
            evidence = Path(args[args.index("--evidence") + 1])
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("ENGINE_OFFLINE_VERIFIED: true\n", encoding="utf-8")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(args)

    monkeypatch.setattr(pai_loop, "_run", fake_run)
    monkeypatch.setattr(pai_loop.tempfile, "TemporaryDirectory", lambda **kwargs: _FixedTempDir())

    pai_loop._materialize_final_evidence("c" * 40)

    cli_call = next(call for call in calls if call[0][1:4] == ["-m", "screen_prove.cli", "offline-complete"])
    assert cli_call[1]["cwd"] == clean_root
    assert (primary_root / "docs" / "reviews" / "ENGINE_OFFLINE_RECEIPT.md").read_text(encoding="utf-8") == "ENGINE_OFFLINE_VERIFIED: true\n"


def test_record_review_request_captures_tree_before_sending(monkeypatch):
    events = []
    state = {}

    monkeypatch.setattr(
        pai_loop,
        "_staged_tree_id",
        lambda: events.append("tree") or "tree-1",
    )
    monkeypatch.setattr(
        pai_loop,
        "_head_id",
        lambda: events.append("head") or "parent-1",
    )
    monkeypatch.setattr(
        pai_loop,
        "_send_review",
        lambda handoff, subject: events.append("send") or "rq-test",
    )

    assert pai_loop._record_review_request(state, "handoff.md", "subject") == "rq-test"
    assert events == ["tree", "head", "send"]
    assert state["reviewed_tree_id"] == "tree-1"
    assert state["reviewed_parent_id"] == "parent-1"


def test_commit_reviewed_tree_uses_captured_tree_and_parent(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "commit-tree"]:
            return type("Result", (), {"returncode": 0, "stdout": "commit-1\n", "stderr": ""})()
        if args[:3] == ["git", "symbolic-ref", "-q"]:
            return type("Result", (), {"returncode": 0, "stdout": "refs/heads/test\n", "stderr": ""})()
        if args[:2] == ["git", "update-ref"]:
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(args)

    monkeypatch.setattr(pai_loop, "_run", fake_run)
    monkeypatch.setattr(pai_loop, "_head_id", lambda: "parent-1")

    assert pai_loop._commit_reviewed_tree("tree-1", "parent-1", "subject") == "commit-1"
    assert calls[0][0] == ["git", "commit-tree", "tree-1", "-p", "parent-1"]
    assert calls[1][0] == ["git", "symbolic-ref", "-q", "HEAD"]
    assert calls[2][0] == ["git", "update-ref", "refs/heads/test", "commit-1", "parent-1"]


def test_approved_review_is_invalidated_when_tree_changes(monkeypatch):
    saved = []
    fresh = []
    state = {
        "pending_request_id": "rq-old",
        "subject": "subject",
        "review_rounds": 0,
        "reviewed_tree_id": "tree-reviewed",
        "reviewed_parent_id": "parent-1",
    }

    monkeypatch.setattr(pai_loop, "_ready", lambda: False)
    monkeypatch.setattr(pai_loop, "_load_state", lambda: dict(state))
    monkeypatch.setattr(
        pai_loop,
        "_review_result",
        lambda request_id: ("VERDICT: APPROVE", "VERDICT: APPROVE"),
    )
    monkeypatch.setattr(pai_loop, "_run", lambda args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(pai_loop, "_staged_tree_id", lambda: "tree-changed")
    monkeypatch.setattr(pai_loop, "_head_id", lambda: "parent-1")
    monkeypatch.setattr(pai_loop, "_run_tests", lambda: "71 passed")
    monkeypatch.setattr(pai_loop, "_status_paths", lambda: {"screen_prove/live_boundary.py"})
    monkeypatch.setattr(pai_loop, "_write_and_stage_handoff", lambda subject, summary, files: "fresh.md")

    def record_review(review_state, handoff, subject):
        fresh.append((handoff, subject))
        review_state["pending_request_id"] = "rq-fresh"
        return "rq-fresh"

    monkeypatch.setattr(pai_loop, "_record_review_request", record_review)
    monkeypatch.setattr(pai_loop, "_save_state", lambda value: saved.append(value))

    result = pai_loop.run_once()

    assert result == "approval invalidated; fresh review requested: rq-fresh"
    assert fresh == [("fresh.md", "staged diff changed after review — fresh review")]
    assert saved[-1]["last_verdict"] == "VERDICT: INVALIDATED"
    assert saved[-1]["pending_request_id"] == "rq-fresh"


def test_review_changes_do_not_auto_block_after_three_rounds(monkeypatch):
    saved = []
    state = {
        "pending_request_id": "rq-old",
        "subject": "subject",
        "review_rounds": 3,
    }

    monkeypatch.setattr(pai_loop, "_ready", lambda: False)
    monkeypatch.setattr(pai_loop, "_load_state", lambda: dict(state))
    monkeypatch.setattr(
        pai_loop,
        "_review_result",
        lambda request_id: ("VERDICT: CHANGES", "VERDICT: CHANGES\n- fix this"),
    )
    monkeypatch.setattr(pai_loop, "_run", lambda args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(pai_loop, "_run_claude", lambda prompt: "fixed")
    monkeypatch.setattr(pai_loop, "_run_tests", lambda: "71 passed")
    monkeypatch.setattr(pai_loop, "_status_paths", lambda: {"tools/pai_loop.py"})
    monkeypatch.setattr(pai_loop, "_unstaged_paths", lambda: set())
    monkeypatch.setattr(pai_loop, "_stage_new_changes", lambda before, before_unstaged: ["tools/pai_loop.py"])
    monkeypatch.setattr(pai_loop, "_write_and_stage_handoff", lambda subject, summary, files: "fresh.md")
    monkeypatch.setattr(pai_loop, "_record_review_request", lambda review_state, handoff, subject: review_state.update({"pending_request_id": "rq-fresh"}) or "rq-fresh")
    monkeypatch.setattr(pai_loop, "_save_state", lambda value: saved.append(value))

    result = pai_loop.run_once()

    assert result == "fixed and re-handed off: rq-fresh"
    assert saved[-1]["review_rounds"] == 4
    assert saved[-1].get("blocked") is not True


def test_review_dispute_fails_closed_for_human_adjudication(monkeypatch):
    saved = []
    state = {
        "pending_request_id": "rq-old",
        "subject": "subject",
        "review_rounds": 1,
    }

    monkeypatch.setattr(pai_loop, "_ready", lambda: False)
    monkeypatch.setattr(pai_loop, "_load_state", lambda: dict(state))
    monkeypatch.setattr(
        pai_loop,
        "_review_result",
        lambda request_id: ("VERDICT: CHANGES", "VERDICT: CHANGES\n- disputed"),
    )
    monkeypatch.setattr(pai_loop, "_run", lambda args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(pai_loop, "_run_claude", lambda prompt: "DISPUTE: reviewer changed the frozen contract")
    monkeypatch.setattr(pai_loop, "_save_state", lambda value: saved.append(value))

    result = pai_loop.run_once()

    assert result == "ESCALATE: Claude disputed a review finding; human adjudication required"
    assert saved[-1]["awaiting_human"] is True
    assert saved[-1]["blocker"] == "DISPUTE: reviewer changed the frozen contract"
