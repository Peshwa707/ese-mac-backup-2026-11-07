import json
import subprocess

import pytest

from screen_prove.cli import main
from screen_prove.contracts import canonical_bytes
from screen_prove.engine_offline import (
    ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH,
    ENGINE_OFFLINE_RECEIPT_VERSION,
    LIVE_API_EXCLUSION,
    build_engine_offline_receipt,
    git_head_info,
    write_engine_offline_evidence,
)


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "offline@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Offline Test"], check=True)


def _commit_all(path, message="init"):
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def _fake_runner(returncode: int, stdout: str):
    def runner(repo_root):
        return subprocess.CompletedProcess(args=["pytest"], returncode=returncode, stdout=stdout, stderr="")

    return runner


# --- git_head_info -------------------------------------------------------------


def test_git_head_info_resolves_commit_and_tree_on_a_committed_repo(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    head = git_head_info(tmp_path)

    assert head["error"] == ""
    assert len(head["commit"]) == 40
    assert len(head["tree"]) == 40


def test_git_head_info_fails_closed_when_there_is_no_commit(tmp_path):
    _init_git_repo(tmp_path)

    head = git_head_info(tmp_path)

    assert head["commit"] == ""
    assert head["tree"] == ""
    assert head["error"] != ""


def test_git_head_info_fails_closed_when_not_a_git_repo(tmp_path):
    head = git_head_info(tmp_path)

    assert head["commit"] == ""
    assert head["tree"] == ""
    assert head["error"] != ""


# --- build_engine_offline_receipt aggregation -----------------------------------


def test_build_engine_offline_receipt_aggregates_every_completed_stage(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt = build_engine_offline_receipt(tmp_path, test_runner=_fake_runner(0, "1 passed"))

    assert receipt["version"] == ENGINE_OFFLINE_RECEIPT_VERSION
    assert receipt["passed"] is True
    assert receipt["doctor"]["passed"] is True
    assert receipt["qualification"]["passed"] is True
    assert receipt["validation"]["passed"] is True
    assert receipt["contract"]["passed"] is True
    assert receipt["cli_contract"]["passed"] is True
    assert receipt["git_head"]["error"] == ""
    assert len(receipt["git_head"]["commit"]) == 40
    assert receipt["live_api_exclusion"] == LIVE_API_EXCLUSION
    assert len(receipt["engine_offline_hash"]) == 64


def test_build_engine_offline_receipt_is_byte_stable_for_the_same_repository_state(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt_a = build_engine_offline_receipt(tmp_path, test_runner=_fake_runner(0, "1 passed"))
    receipt_b = build_engine_offline_receipt(tmp_path, test_runner=_fake_runner(0, "1 passed"))

    assert canonical_bytes(receipt_a) == canonical_bytes(receipt_b)


def test_build_engine_offline_receipt_ignores_pytest_wall_clock_duration(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt_fast = build_engine_offline_receipt(
        tmp_path,
        test_runner=_fake_runner(0, "347 passed in 1.01s"),
    )
    receipt_slow = build_engine_offline_receipt(
        tmp_path,
        test_runner=_fake_runner(0, "347 passed in 9.87s"),
    )

    assert canonical_bytes(receipt_fast) == canonical_bytes(receipt_slow)
    assert receipt_fast["doctor"]["checks"][3]["detail"] == "347 passed"


def test_build_engine_offline_receipt_fails_when_the_doctor_report_fails(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    receipt = build_engine_offline_receipt(tmp_path, test_runner=_fake_runner(0, "1 passed"))

    assert receipt["passed"] is False
    assert receipt["doctor"]["passed"] is False


def test_build_engine_offline_receipt_fails_when_git_head_cannot_be_resolved(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")

    receipt = build_engine_offline_receipt(tmp_path, test_runner=_fake_runner(0, "1 passed"))

    assert receipt["passed"] is False
    assert receipt["git_head"]["error"] != ""


def test_build_engine_offline_receipt_fails_when_the_injected_test_runner_fails(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt = build_engine_offline_receipt(tmp_path, test_runner=_fake_runner(1, "1 failed"))

    assert receipt["passed"] is False
    by_name = {check["name"]: check for check in receipt["doctor"]["checks"]}
    assert by_name["tests_pass"]["passed"] is False


def test_build_engine_offline_receipt_fails_closed_when_tests_are_skipped(tmp_path):
    # Stage 11 is the hard-stop completion gate and requires current-head
    # full-suite evidence: skip_tests=True must never certify completion,
    # even when the doctor/qualification `tests_pass` checks it drives would
    # otherwise report `passed: True` for having been skipped "by request".
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt = build_engine_offline_receipt(tmp_path, skip_tests=True)

    assert receipt["tests_skipped"] is True
    assert receipt["passed"] is False
    # The underlying checks are otherwise clean; only the skip_tests gate
    # forces the aggregate receipt closed.
    assert receipt["doctor"]["passed"] is True
    assert receipt["qualification"]["passed"] is True
    assert receipt["git_head"]["error"] == ""


def test_write_engine_offline_evidence_refuses_a_failed_receipt(tmp_path):
    receipt = build_engine_offline_receipt(tmp_path, skip_tests=True)
    assert receipt["passed"] is False

    with pytest.raises(ValueError):
        write_engine_offline_evidence(tmp_path / "ENGINE_OFFLINE_RECEIPT.md", receipt)

    assert not (tmp_path / "ENGINE_OFFLINE_RECEIPT.md").exists()


def test_engine_offline_receipt_documents_explicit_live_api_exclusion():
    assert LIVE_API_EXCLUSION["network_access"] is False
    assert LIVE_API_EXCLUSION["live_credentials_read"] is False
    assert LIVE_API_EXCLUSION["live_providers_called"] is False
    assert isinstance(LIVE_API_EXCLUSION["detail"], str) and LIVE_API_EXCLUSION["detail"].strip()


# --- CLI integration -------------------------------------------------------------


def test_cli_offline_complete_writes_report_and_exits_zero_on_a_clean_repository(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    _commit_all(tmp_path)
    report_path = tmp_path / "engine-offline-report.json"
    evidence_path = tmp_path / "docs" / "reviews" / "ENGINE_OFFLINE_RECEIPT.md"

    exit_code = main(
        [
            "offline-complete",
            "--repo-root",
            str(tmp_path),
            "--output",
            str(report_path),
            "--evidence",
            str(evidence_path),
        ]
    )
    assert exit_code == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["version"] == ENGINE_OFFLINE_RECEIPT_VERSION
    assert "ENGINE_OFFLINE_VERIFIED: true" in evidence_path.read_text(encoding="utf-8")


def test_cli_offline_complete_writes_the_required_deliverable_at_the_default_path(tmp_path):
    # `docs/AUTONOMOUS_BUILD_LOOP.md` requires
    # `docs/reviews/ENGINE_OFFLINE_RECEIPT.md` with `ENGINE_OFFLINE_VERIFIED:
    # true` as the stage-11 deliverable; without an explicit `--evidence`
    # override it must land at that fixed path under `--repo-root`.
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    _commit_all(tmp_path)

    exit_code = main(["offline-complete", "--repo-root", str(tmp_path)])
    assert exit_code == 0

    evidence_path = tmp_path / ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH
    assert "ENGINE_OFFLINE_VERIFIED: true" in evidence_path.read_text(encoding="utf-8")


def test_cli_offline_complete_exits_nonzero_on_a_dirty_repository(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    exit_code = main(["offline-complete", "--repo-root", str(tmp_path)])
    assert exit_code == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert not (tmp_path / ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH).exists()


def test_cli_offline_complete_prints_report_without_an_output_path(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    _commit_all(tmp_path)

    exit_code = main(["offline-complete", "--repo-root", str(tmp_path)])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
