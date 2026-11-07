import json
import subprocess

from screen_prove.cli import main
from screen_prove.contracts import IntegrityError, canonical_bytes
import screen_prove.qualification as qualification
from screen_prove.live_boundary import LIVE_SMOKE_OPT_IN_ENV_VAR
from screen_prove.qualification import (
    QUALIFICATION_RECEIPT_VERSION,
    build_qualification_receipt,
    check_cli_command_surface_replay,
    check_extraction_replay,
    check_live_boundary_default_denial,
    check_public_canary_fixture_validation,
    check_risk_of_bias_replay,
    check_screening_end_to_end_replay,
    check_synthesis_replay,
)

ALL_STAGE_NAMES = {
    "live_boundary_default_denial",
    "public_canary_fixture_validation",
    "extraction_replay",
    "risk_of_bias_replay",
    "synthesis_replay",
    "screening_end_to_end_replay",
    "cli_command_surface_replay",
}


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "qualify@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Qualify Test"], check=True)


def _commit_all(path, message="init"):
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def _fake_runner(returncode: int, stdout: str):
    def runner(repo_root):
        return subprocess.CompletedProcess(args=["pytest"], returncode=returncode, stdout=stdout, stderr="")

    return runner


# --- individual stage checks: happy path -------------------------------------


def test_check_extraction_replay_passes_on_a_fresh_fixture():
    result = check_extraction_replay()
    assert result.passed is True
    assert result.name == "extraction_replay"
    assert len(result.artifact_hash) == 64


def test_check_risk_of_bias_replay_passes_on_a_fresh_fixture():
    result = check_risk_of_bias_replay()
    assert result.passed is True
    assert result.name == "risk_of_bias_replay"
    assert len(result.artifact_hash) == 64


def test_check_synthesis_replay_passes_on_a_fresh_fixture():
    result = check_synthesis_replay()
    assert result.passed is True
    assert result.name == "synthesis_replay"
    assert len(result.artifact_hash) == 64


def test_check_screening_end_to_end_replay_passes_on_a_fresh_fixture():
    result = check_screening_end_to_end_replay()
    assert result.passed is True
    assert result.name == "screening_end_to_end_replay"
    assert len(result.artifact_hash) == 64


def test_check_cli_command_surface_replay_passes_on_a_fresh_fixture():
    result = check_cli_command_surface_replay()
    assert result.passed is True
    assert result.name == "cli_command_surface_replay"
    assert len(result.artifact_hash) == 64


def test_check_live_boundary_default_denial_passes_on_the_real_module():
    result = check_live_boundary_default_denial()
    assert result.passed is True
    assert result.name == "live_boundary_default_denial"
    assert len(result.artifact_hash) == 64


def test_check_public_canary_fixture_validation_passes_on_the_real_module():
    result = check_public_canary_fixture_validation()
    assert result.passed is True
    assert result.name == "public_canary_fixture_validation"
    assert len(result.artifact_hash) == 64


# --- individual stage checks: fail closed if tamper detection regresses -----


def test_check_extraction_replay_fails_closed_if_verification_stops_detecting_tampering(monkeypatch):
    monkeypatch.setattr(qualification, "verify_extraction_record", lambda record: record)
    result = check_extraction_replay()
    assert result.passed is False
    assert "not detected" in result.detail


def test_check_risk_of_bias_replay_fails_closed_if_verification_stops_detecting_tampering(monkeypatch):
    monkeypatch.setattr(qualification, "verify_rob_assessment", lambda assessment: assessment)
    result = check_risk_of_bias_replay()
    assert result.passed is False
    assert "not detected" in result.detail


def test_check_synthesis_replay_fails_closed_if_verification_stops_detecting_tampering(monkeypatch):
    monkeypatch.setattr(qualification, "verify_calculation", lambda calculation: calculation)
    result = check_synthesis_replay()
    assert result.passed is False
    assert "not detected" in result.detail


def test_check_screening_end_to_end_replay_fails_closed_if_verification_stops_detecting_tampering(monkeypatch):
    monkeypatch.setattr(qualification, "verify_replay", lambda *args, **kwargs: True)
    result = check_screening_end_to_end_replay()
    assert result.passed is False
    assert "not detected" in result.detail


def test_check_cli_command_surface_replay_fails_closed_if_verification_stops_detecting_tampering(monkeypatch):
    import screen_prove.cli_contract as cli_contract

    monkeypatch.setattr(cli_contract, "verify_cli_command_fixtures", lambda bundle: dict(bundle))
    result = check_cli_command_surface_replay()
    assert result.passed is False
    assert "not detected" in result.detail


def test_check_cli_command_surface_replay_reports_an_unexpected_integrity_error(monkeypatch):
    import screen_prove.cli_contract as cli_contract

    def raise_integrity_error():
        raise IntegrityError("simulated failure")

    monkeypatch.setattr(cli_contract, "build_cli_command_fixtures", raise_integrity_error)
    result = check_cli_command_surface_replay()
    assert result.passed is False
    assert "CLI command surface replay failed" in result.detail


def test_check_live_boundary_default_denial_fails_closed_if_the_missing_opt_in_gate_regresses(monkeypatch):
    monkeypatch.setattr(qualification, "live_smoke_authorized", lambda env=None: True)
    result = check_live_boundary_default_denial()
    assert result.passed is False
    assert "authorized" in result.detail


def test_check_live_boundary_default_denial_fails_closed_if_a_near_miss_value_is_authorized(monkeypatch):
    real_live_smoke_authorized = qualification.live_smoke_authorized

    def loose_authorized(env=None):
        if env and env.get(LIVE_SMOKE_OPT_IN_ENV_VAR) == "true":
            return True
        return real_live_smoke_authorized(env)

    monkeypatch.setattr(qualification, "live_smoke_authorized", loose_authorized)
    result = check_live_boundary_default_denial()
    assert result.passed is False
    assert "near-miss" in result.detail


def test_check_live_boundary_default_denial_fails_closed_if_the_bypass_rejection_regresses(monkeypatch):
    class _PermissiveBase:
        def __init_subclass__(cls, **kwargs):
            pass

        def execute_live(self, request):
            return {}

    monkeypatch.setattr(qualification, "LiveBoundaryAdapter", _PermissiveBase)
    result = check_live_boundary_default_denial()
    assert result.passed is False
    assert "not rejected" in result.detail


def test_check_public_canary_fixture_validation_fails_closed_if_identity_mismatch_detection_regresses(monkeypatch):
    class _BlindCanary(qualification.PubMedArticleCanary):
        def verify_response(self, params, response):
            return None

    monkeypatch.setattr(qualification, "PubMedArticleCanary", _BlindCanary)
    result = check_public_canary_fixture_validation()
    assert result.passed is False
    assert "not detected" in result.detail


def test_check_extraction_replay_reports_an_unexpected_integrity_error(monkeypatch):
    def raise_integrity_error(*args, **kwargs):
        raise IntegrityError("simulated failure")

    monkeypatch.setattr(qualification, "build_extraction_record", raise_integrity_error)
    result = check_extraction_replay()
    assert result.passed is False
    assert "extraction replay failed" in result.detail


# --- build_qualification_receipt aggregation ---------------------------------


def test_build_qualification_receipt_aggregates_doctor_and_stage_checks(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt = build_qualification_receipt(tmp_path, skip_tests=True)

    assert receipt["version"] == QUALIFICATION_RECEIPT_VERSION
    assert receipt["passed"] is True
    assert receipt["doctor"]["passed"] is True
    stage_names = {check["name"] for check in receipt["stage_checks"]}
    assert stage_names == ALL_STAGE_NAMES
    assert all(check["passed"] for check in receipt["stage_checks"])
    assert all(len(check["artifact_hash"]) == 64 for check in receipt["stage_checks"])


def test_build_qualification_receipt_is_byte_stable_for_the_same_repository_state(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt_a = build_qualification_receipt(tmp_path, skip_tests=True)
    receipt_b = build_qualification_receipt(tmp_path, skip_tests=True)

    assert canonical_bytes(receipt_a) == canonical_bytes(receipt_b)


def test_build_qualification_receipt_fails_when_the_doctor_report_fails(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    receipt = build_qualification_receipt(tmp_path, skip_tests=True)
    assert receipt["passed"] is False
    assert receipt["doctor"]["passed"] is False


def test_build_qualification_receipt_uses_the_injected_test_runner(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)

    receipt = build_qualification_receipt(tmp_path, test_runner=_fake_runner(1, "1 failed"))
    assert receipt["passed"] is False
    by_name = {check["name"]: check for check in receipt["doctor"]["checks"]}
    assert by_name["tests_pass"]["passed"] is False


# --- CLI integration -----------------------------------------------------------


def test_cli_qualify_writes_report_and_exits_zero_on_a_clean_repository(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    report_path = tmp_path / "qualification-report.json"

    exit_code = main(
        ["qualify", "--repo-root", str(tmp_path), "--skip-tests", "--output", str(report_path)]
    )
    assert exit_code == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["version"] == QUALIFICATION_RECEIPT_VERSION


def test_cli_qualify_exits_nonzero_on_a_dirty_repository(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "committed.txt").write_text("content\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "stray.txt").write_text("oops\n", encoding="utf-8")

    exit_code = main(["qualify", "--repo-root", str(tmp_path), "--skip-tests"])
    assert exit_code == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
