"""Engine offline completion receipt: stage 11 of the work-package queue in
`docs/AUTONOMOUS_BUILD_LOOP.md`.

This module adds no new engine calculation behavior. It aggregates the
already-completed offline evidence chain -- the repository readiness doctor
report (stage 1, `screen_prove.doctor.run_doctor`), the end-to-end
qualification receipt covering every other completed governed stage
(`screen_prove.qualification.build_qualification_receipt`), the full
normal/missing/ambiguous/malformed/boundary/tamper validation matrix (stage
8, `screen_prove.validation.build_validation_receipt`), the engine/WebUI
compatibility contract (stage 9, `screen_prove.contract.build_contract_receipt`),
and the CLI command-shape contract (stage 10,
`screen_prove.cli_contract.build_cli_contract_receipt`) -- into one receipt
that also records the current git commit/tree this checkout is built from
(`git_head_info`) and an explicit, fixed statement that no live API,
credential, or network call was made while building it (`LIVE_API_EXCLUSION`).

`build_engine_offline_receipt` reports `passed: True` only if every
aggregated check passed *and* the git HEAD commit/tree could be resolved
cleanly (an uncommitted working tree, or a directory that is not a git
repository at all, fails this receipt closed rather than silently omitting
provenance) *and* `skip_tests` is False. Stage 11 is the hard-stop
completion gate for this checkout (`docs/AUTONOMOUS_BUILD_LOOP.md`): it
requires current-head full-suite evidence, so `skip_tests=True` -- which
would otherwise let the aggregated doctor/qualification `tests_pass` checks
report `passed: True` without ever invoking `pytest` -- always fails this
receipt closed instead of letting a skipped test run silently certify
completion. `skip_tests=True` exists only so this module's own aggregation
wiring can be exercised quickly in a unit test; it can never yield
`passed: True` and must never be used to produce a real completion receipt.
Every check is offline: no network socket is opened, and no live credential
is read; `repo_root` is never modified.

`write_engine_offline_evidence` turns a built receipt into the fixed stage-11
deliverable, `docs/reviews/ENGINE_OFFLINE_RECEIPT.md` (see
`ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH`), containing an explicit
`ENGINE_OFFLINE_VERIFIED: true` line plus the commit/tree and receipt-hash
evidence it was built from. It raises `ValueError` instead of writing
anything for a receipt whose `passed` is not `True` -- a failed or
tests-skipped completion attempt never leaves a deliverable claiming
`ENGINE_OFFLINE_VERIFIED: true` on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .contract import build_contract_receipt
from .contracts import object_hash
from .doctor import GIT_TIMEOUT_SECONDS, TestRunner, _default_test_runner, run_doctor
from .qualification import build_qualification_receipt
from .validation import build_validation_receipt

ENGINE_OFFLINE_RECEIPT_VERSION = "engine-offline-receipt-v1"

# The fixed stage-11 deliverable path (`docs/AUTONOMOUS_BUILD_LOOP.md`),
# relative to the repository root the receipt was built for.
ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH = Path("docs") / "reviews" / "ENGINE_OFFLINE_RECEIPT.md"

# A fixed, versioned statement of what this receipt does and does not prove
# about live-API exclusion. It is never derived from ambient process state
# (no environment variable is read here) -- it documents the invariant that
# every function this module calls (doctor, qualification, validation,
# contract, and CLI-contract) is itself built only from fixed offline
# fixtures, local git subprocess calls, and local filesystem I/O.
LIVE_API_EXCLUSION: dict[str, Any] = {
    "network_access": False,
    "live_credentials_read": False,
    "live_providers_called": False,
    "detail": (
        "This receipt is built entirely from offline fixtures and the local "
        "git working tree; no network socket is opened, no live credential is "
        "read, and no PubMed, ClinicalTrials.gov, LLM, institutional, or "
        "subscription connector is called."
    ),
}


def git_head_info(repo_root: Path) -> dict[str, str]:
    """Resolve `repo_root`'s current git HEAD commit and tree hashes.

    Never raises: a git failure (not a repository, no commit yet, `git`
    unavailable, or a timeout) is reported as an explicit non-empty `error`
    with empty `commit`/`tree` fields, so a caller can fail closed instead of
    crashing or silently omitting provenance.
    """
    repo_root = Path(repo_root)
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        tree = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"commit": "", "tree": "", "error": f"unable to resolve git HEAD: {exc}"}
    if commit.returncode != 0 or tree.returncode != 0:
        detail = commit.stderr.strip() or tree.stderr.strip()
        return {"commit": "", "tree": "", "error": f"'git rev-parse' failed: {detail}"}
    return {"commit": commit.stdout.strip(), "tree": tree.stdout.strip(), "error": ""}


def build_engine_offline_receipt(
    repo_root: Path,
    *,
    skip_tests: bool = False,
    test_runner: TestRunner = _default_test_runner,
) -> dict[str, Any]:
    """Build a byte-stable receipt aggregating every completed offline stage.

    `passed` is True only if the doctor report, the qualification receipt,
    the validation receipt, the engine/WebUI contract receipt, the CLI
    command-shape contract receipt, and `git_head_info` all resolve cleanly,
    *and* `skip_tests` is False. `skip_tests=True` always forces `passed` to
    False (recorded explicitly in `tests_skipped`) -- stage 11 is the
    hard-stop completion gate and requires current-head full-suite evidence,
    so a skipped test run can never certify completion, even if every other
    aggregated check would otherwise pass. `repo_root` is never modified.
    """
    # Imported lazily (inside this function, not at module level) because
    # `screen_prove.cli_contract` imports `screen_prove.cli.main` to drive
    # the subcommands it exercises, and `screen_prove.cli` imports this
    # `engine_offline` module at CLI-startup time -- a module-level import
    # here would be circular (the same reason `qualification.py`'s
    # `check_cli_command_surface_replay` imports it lazily).
    from .cli_contract import build_cli_contract_receipt

    repo_root = Path(repo_root)
    doctor_report = run_doctor(repo_root, skip_tests=skip_tests, test_runner=test_runner)
    qualification_receipt = build_qualification_receipt(
        repo_root, skip_tests=skip_tests, test_runner=test_runner
    )
    validation_receipt = build_validation_receipt()
    contract_receipt = build_contract_receipt()
    cli_contract_receipt = build_cli_contract_receipt()
    git_head = git_head_info(repo_root)

    passed = (
        doctor_report.passed
        and qualification_receipt["passed"]
        and validation_receipt["passed"]
        and contract_receipt["passed"]
        and cli_contract_receipt["passed"]
        and not git_head["error"]
        and not skip_tests
    )
    body = {
        "version": ENGINE_OFFLINE_RECEIPT_VERSION,
        "git_head": git_head,
        "doctor": doctor_report.to_dict(),
        "qualification": qualification_receipt,
        "validation": validation_receipt,
        "contract": contract_receipt,
        "cli_contract": cli_contract_receipt,
        "live_api_exclusion": LIVE_API_EXCLUSION,
        "tests_skipped": skip_tests,
        "passed": passed,
    }
    return {**body, "engine_offline_hash": object_hash(body)}


def write_engine_offline_evidence(path: Path, receipt: dict[str, Any]) -> None:
    """Write the final human-readable evidence file only for a passing receipt."""
    if receipt.get("version") != ENGINE_OFFLINE_RECEIPT_VERSION or receipt.get("passed") is not True:
        raise ValueError("cannot write engine offline evidence from a failed receipt")
    git_head = receipt.get("git_head", {})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Engine offline completion receipt",
                "",
                "ENGINE_OFFLINE_VERIFIED: true",
                "",
                f"- Commit: `{git_head.get('commit', '')}`",
                f"- Tree: `{git_head.get('tree', '')}`",
                f"- Engine receipt hash: `{receipt['engine_offline_hash']}`",
                "- Verification: current-head doctor, qualification, validation, "
                "artifact contract, CLI contract, and full offline test evidence passed.",
                "- Live boundary: no network, credentials, PubMed, ClinicalTrials.gov, "
                "LLM, institutional, or subscription connector was used.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
