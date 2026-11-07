"""Offline pre-live readiness ("doctor") checks for the Screen-and-Prove CLI.

Every check here is repository-scoped and network-free: it inspects the local
package installation, the local git working tree, and files that are tracked
or addable in this repository. Nothing here reads environment credentials,
opens a network socket, or calls a live provider.
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

PACKAGE_NAME = "evidence-synthesis-engine-screen-prove"
CONSOLE_SCRIPT_NAME = "screen-prove"
CONSOLE_SCRIPT_TARGET = "screen_prove.cli:main"

GIT_TIMEOUT_SECONDS = 30
TEST_TIMEOUT_SECONDS = 600
TEXT_FILE_MAX_BYTES = 2_000_000
MAX_FINDINGS_SHOWN = 5

CREDENTIAL_FILENAME_PATTERN = re.compile(
    r"(^|/)(\.env(\..+)?|credentials\.(json|ya?ml)|secrets\.(json|ya?ml)|\.netrc|[^/]*\.pem|[^/]*_rsa)$",
    re.IGNORECASE,
)

_SECRET_VALUE = r"(sk-[A-Za-z0-9_-]{10,}|[A-Za-z0-9]{20,})"
CREDENTIAL_CONTENT_PATTERNS = (
    re.compile(rf"(ANTHROPIC|OPENAI|NCBI|PUBMED|CLINICALTRIALS)_API_KEY\s*[:=]\s*['\"]?{_SECRET_VALUE}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "checks": [check.to_dict() for check in self.checks]}


def check_package_installed() -> CheckResult:
    try:
        distribution = importlib.metadata.distribution(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return CheckResult(
            "package_installed",
            False,
            f"'{PACKAGE_NAME}' is not installed (run: pip install -e \".[dev]\")",
        )

    scripts = [
        ep
        for ep in distribution.entry_points
        if ep.group == "console_scripts" and ep.name == CONSOLE_SCRIPT_NAME
    ]
    if not any(ep.value == CONSOLE_SCRIPT_TARGET for ep in scripts):
        return CheckResult(
            "package_installed",
            False,
            f"'{PACKAGE_NAME}' does not own console script '{CONSOLE_SCRIPT_NAME}' "
            f"with target '{CONSOLE_SCRIPT_TARGET}'",
        )
    return CheckResult(
        "package_installed",
        True,
        f"{PACKAGE_NAME} {distribution.version} installed with console script '{CONSOLE_SCRIPT_NAME}'",
    )


def check_cli_smoke() -> CheckResult:
    from .cli import main as cli_main

    with tempfile.TemporaryDirectory(prefix="screen-prove-doctor-smoke-") as tmp:
        target = Path(tmp) / "smoke"
        try:
            exit_code = cli_main(["init", str(target)])
        except Exception as exc:  # noqa: BLE001 - a doctor check must never crash
            return CheckResult("cli_smoke", False, f"'screen-prove init' raised {exc!r}")
        expected = ("protocol.json", "procedure.json", "schema.json")
        missing = [name for name in expected if not (target / name).exists()]
        if exit_code != 0 or missing:
            return CheckResult(
                "cli_smoke",
                False,
                f"'screen-prove init' exit_code={exit_code} missing_files={missing}",
            )
    return CheckResult("cli_smoke", True, "'screen-prove init' produced protocol/procedure/schema files")


def _run_git(repo_root: Path, args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def check_repository_clean(repo_root: Path) -> CheckResult:
    try:
        result = _run_git(repo_root, ["status", "--porcelain"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("repository_clean", False, f"unable to run 'git status': {exc}")
    if result.returncode != 0:
        return CheckResult("repository_clean", False, f"'git status' failed: {result.stderr.strip()}")
    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    if dirty:
        preview = ", ".join(dirty[:MAX_FINDINGS_SHOWN])
        more = "" if len(dirty) <= MAX_FINDINGS_SHOWN else f" (+{len(dirty) - MAX_FINDINGS_SHOWN} more)"
        return CheckResult("repository_clean", False, f"{len(dirty)} dirty path(s): {preview}{more}")
    return CheckResult("repository_clean", True, "working tree clean")


def check_no_remotes(repo_root: Path) -> CheckResult:
    try:
        result = _run_git(repo_root, ["remote"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("no_remotes", False, f"unable to run 'git remote': {exc}")
    if result.returncode != 0:
        return CheckResult("no_remotes", False, f"'git remote' failed: {result.stderr.strip()}")
    remotes = [line for line in result.stdout.splitlines() if line.strip()]
    if remotes:
        return CheckResult("no_remotes", False, f"remote(s) configured: {', '.join(remotes)}")
    return CheckResult("no_remotes", True, "no git remotes configured")


def _project_files(repo_root: Path) -> list[str]:
    result = _run_git(repo_root, ["ls-files", "--cached", "--others", "--exclude-standard"])
    if result.returncode != 0:
        raise RuntimeError(f"'git ls-files' failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def check_no_live_credentials(repo_root: Path) -> CheckResult:
    try:
        files = _project_files(repo_root)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return CheckResult("no_live_credentials", False, f"unable to list project files: {exc}")

    findings: list[str] = []
    for rel_path in files:
        if CREDENTIAL_FILENAME_PATTERN.search(rel_path):
            findings.append(f"{rel_path}: credential-shaped filename")
            continue
        full_path = repo_root / rel_path
        try:
            if full_path.is_symlink() or not full_path.is_file():
                findings.append(f"{rel_path}: not a regular file; unable to inspect")
                continue
            size = full_path.stat().st_size
            if size > TEXT_FILE_MAX_BYTES:
                findings.append(
                    f"{rel_path}: file is {size} bytes, above the {TEXT_FILE_MAX_BYTES}-byte inspection limit"
                )
                continue
            text = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"{rel_path}: unreadable or not valid UTF-8; unable to inspect")
            continue
        if any(pattern.search(text) for pattern in CREDENTIAL_CONTENT_PATTERNS):
            findings.append(f"{rel_path}: matches a live-credential pattern")

    if findings:
        preview = "; ".join(findings[:MAX_FINDINGS_SHOWN])
        more = "" if len(findings) <= MAX_FINDINGS_SHOWN else f" (+{len(findings) - MAX_FINDINGS_SHOWN} more)"
        return CheckResult("no_live_credentials", False, f"{len(findings)} finding(s): {preview}{more}")
    return CheckResult(
        "no_live_credentials",
        True,
        f"scanned {len(files)} project file(s), no live-credential patterns found",
    )


TestRunner = Callable[[Path], subprocess.CompletedProcess]


def _default_test_runner(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=TEST_TIMEOUT_SECONDS,
    )


def check_tests_pass(
    repo_root: Path,
    *,
    skip: bool = False,
    runner: TestRunner = _default_test_runner,
) -> CheckResult:
    if skip:
        return CheckResult("tests_pass", True, "skipped by request (--skip-tests)")
    try:
        result = runner(repo_root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("tests_pass", False, f"unable to run 'pytest -q': {exc}")
    output_lines = [line for line in result.stdout.splitlines() if line.strip()]
    summary = output_lines[-1] if output_lines else ""
    # Pytest appends wall-clock duration (for example, ``in 2.31s``). That
    # value is useful in a terminal but is volatile and must not enter a
    # content-addressed offline receipt.
    summary = re.sub(r"\s+in\s+\d+(?:\.\d+)?s\s*$", "", summary)
    if result.returncode != 0:
        return CheckResult("tests_pass", False, f"pytest exited {result.returncode}: {summary}")
    return CheckResult("tests_pass", True, summary or "pytest exited 0")


def run_doctor(
    repo_root: Path,
    *,
    skip_tests: bool = False,
    test_runner: TestRunner = _default_test_runner,
) -> DoctorReport:
    """Run every pre-live readiness check and return an aggregate report.

    Every check is offline and repository-scoped: no network access, no
    credential reads, and no changes to `repo_root`.
    """
    repo_root = Path(repo_root)
    checks = (
        check_package_installed(),
        check_cli_smoke(),
        check_repository_clean(repo_root),
        check_tests_pass(repo_root, skip=skip_tests, runner=test_runner),
        check_no_remotes(repo_root),
        check_no_live_credentials(repo_root),
    )
    return DoctorReport(checks=checks)
