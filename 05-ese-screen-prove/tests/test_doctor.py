import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from screen_prove.cli import main
from screen_prove.doctor import (
    check_cli_smoke,
    check_no_live_credentials,
    check_no_remotes,
    check_package_installed,
    check_repository_clean,
    check_tests_pass,
    run_doctor,
)
import screen_prove.doctor as doctor


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "doctor@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Doctor Test"], check=True)


def _commit_all(path, message="init"):
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def _fake_runner(returncode: int, stdout: str):
    def runner(repo_root):
        return subprocess.CompletedProcess(args=["pytest"], returncode=returncode, stdout=stdout, stderr="")

    return runner


def test_check_package_installed_finds_console_script():
    result = check_package_installed()
    assert result.name == "package_installed"
    assert result.passed is True
    assert "screen-prove" in result.detail


def test_check_package_installed_rejects_same_named_script_owned_by_another_target(monkeypatch):
    distribution = SimpleNamespace(
        version="0.1.0",
        entry_points=(
            SimpleNamespace(
                group="console_scripts", name="screen-prove", value="unrelated:main"
            ),
        ),
    )
    monkeypatch.setattr(doctor.importlib.metadata, "distribution", lambda name: distribution)

    result = doctor.check_package_installed()
    assert result.passed is False
    assert "does not own" in result.detail


def test_check_cli_smoke_runs_init_in_a_throwaway_directory():
    result = check_cli_smoke()
    assert result.passed is True
    assert "protocol" in result.detail


def test_check_repository_clean_passes_on_a_committed_repo(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    result = check_repository_clean(tmp_path)
    assert result.passed is True


def test_check_repository_clean_fails_on_untracked_file(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    result = check_repository_clean(tmp_path)
    assert result.passed is False
    assert "stray.txt" in result.detail


def test_check_no_remotes_passes_with_no_remote_configured(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    result = check_no_remotes(tmp_path)
    assert result.passed is True


def test_check_no_remotes_fails_when_a_remote_is_configured(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "https://example.test/repo.git"],
        check=True,
    )

    result = check_no_remotes(tmp_path)
    assert result.passed is False
    assert "origin" in result.detail


def test_check_no_live_credentials_passes_on_clean_project(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("no secrets here\n", encoding="utf-8")
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is True


def test_check_no_live_credentials_flags_a_credential_shaped_filename(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("no secrets here\n", encoding="utf-8")
    (tmp_path / "credentials.json").write_text("{}\n", encoding="utf-8")
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is False
    assert "credentials.json" in result.detail


def _fake_secret() -> str:
    # Assembled at runtime so this file's own source text never contains a
    # contiguous string that the credential scanner would flag on itself.
    return "sk-ant-" + "abcdefghij0123456789"


def test_check_no_live_credentials_flags_a_live_looking_api_key_value(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "config.py").write_text(
        f'ANTHROPIC_API_KEY = "{_fake_secret()}"\n', encoding="utf-8"
    )
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is False
    assert "config.py" in result.detail


def test_check_no_live_credentials_ignores_placeholder_text(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text(
        "Set ANTHROPIC_API_KEY=your-key-here before running live smoke tests.\n",
        encoding="utf-8",
    )
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is True


def test_check_no_live_credentials_ignores_gitignored_env_files(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("no secrets here\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={_fake_secret()}\n", encoding="utf-8")

    result = check_no_live_credentials(tmp_path)
    assert result.passed is True


def test_check_no_live_credentials_fails_closed_on_an_oversized_file(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "large.txt").write_bytes(b"x" * (2_000_001))
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is False
    assert "large.txt" in result.detail
    assert "inspection limit" in result.detail


def test_check_no_live_credentials_fails_closed_on_invalid_utf8(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "invalid.txt").write_bytes(b"not utf-8: \xff\n")
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is False
    assert "invalid.txt" in result.detail
    assert "unable to inspect" in result.detail


def test_check_no_live_credentials_fails_closed_on_read_error(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    (tmp_path / "unreadable.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    original_read_text = Path.read_text

    def raise_read_error(self, *args, **kwargs):
        if self.name == "unreadable.txt":
            raise OSError("simulated read failure")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raise_read_error)
    result = check_no_live_credentials(tmp_path)
    assert result.passed is False
    assert "unreadable.txt" in result.detail
    assert "unable to inspect" in result.detail


def test_check_no_live_credentials_fails_closed_on_a_symlink(tmp_path):
    _init_git_repo(tmp_path)
    outside = tmp_path.parent / "doctor-outside-secret.txt"
    outside.write_text("outside content\n", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)
    _commit_all(tmp_path)

    result = check_no_live_credentials(tmp_path)
    assert result.passed is False
    assert "linked.txt" in result.detail
    assert "not a regular file" in result.detail


def test_check_tests_pass_reports_failure_from_injected_runner(tmp_path):
    result = check_tests_pass(tmp_path, runner=_fake_runner(1, "1 failed, 2 passed"))
    assert result.passed is False
    assert "1 failed" in result.detail


def test_check_tests_pass_reports_success_from_injected_runner(tmp_path):
    result = check_tests_pass(tmp_path, runner=_fake_runner(0, "3 passed"))
    assert result.passed is True
    assert "3 passed" in result.detail


def test_check_tests_pass_can_be_skipped():
    result = check_tests_pass("/nonexistent", skip=True)
    assert result.passed is True
    assert "skipped" in result.detail


def test_run_doctor_aggregates_all_checks_and_fails_on_a_dirty_repo(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    report = run_doctor(tmp_path, skip_tests=True)
    names = {check.name for check in report.checks}
    assert names == {
        "package_installed",
        "cli_smoke",
        "repository_clean",
        "tests_pass",
        "no_remotes",
        "no_live_credentials",
    }
    assert report.passed is False
    by_name = {check.name: check for check in report.checks}
    assert by_name["repository_clean"].passed is False
    assert by_name["tests_pass"].passed is True  # skipped, not run


def test_run_doctor_passes_on_a_clean_repo_with_tests_skipped(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    report = run_doctor(tmp_path, skip_tests=True)
    assert report.passed is True
    assert report.to_dict()["passed"] is True


def test_cli_doctor_exits_nonzero_on_a_dirty_repository(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    exit_code = main(["doctor", "--repo-root", str(tmp_path), "--skip-tests"])
    assert exit_code == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False


def test_cli_doctor_writes_report_and_exits_zero_on_a_clean_repository(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    report_path = tmp_path / "doctor-report.json"

    exit_code = main(
        ["doctor", "--repo-root", str(tmp_path), "--skip-tests", "--output", str(report_path)]
    )
    assert exit_code == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert {check["name"] for check in payload["checks"]} >= {"package_installed", "cli_smoke"}


@pytest.mark.skipif(sys.platform == "win32", reason="posix-only git smoke assumptions")
def test_check_repository_clean_fails_closed_when_not_a_git_repo(tmp_path):
    result = check_repository_clean(tmp_path)
    assert result.passed is False
