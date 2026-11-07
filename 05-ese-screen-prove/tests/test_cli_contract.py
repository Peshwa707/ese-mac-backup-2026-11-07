import json

from screen_prove.cli import main
from screen_prove.contracts import IntegrityError, canonical_bytes, object_hash
from screen_prove.cli_contract import (
    CLI_COMMAND_CONTRACTS,
    CLI_CONTRACT_RECEIPT_VERSION,
    CLI_CONTRACT_VERSION,
    build_cli_command_fixtures,
    build_cli_contract_receipt,
    describe_cli_contract,
    verify_cli_command_fixtures,
)


# --- the documented CLI command-shape contract itself -------------------------


def test_describe_cli_contract_lists_every_run_level_cli_surface():
    contract = describe_cli_contract()
    assert contract["version"] == CLI_CONTRACT_VERSION
    assert set(contract["commands"]) == {
        "extraction_record",
        "risk_of_bias_assessment",
        "synthesis_calculation",
    }
    assert contract["commands"]["extraction_record"]["command"] == "extract"
    assert contract["commands"]["risk_of_bias_assessment"]["command"] == "rob"
    assert contract["commands"]["synthesis_calculation"]["command"] == "synthesize"
    for entry in contract["commands"].values():
        assert set(entry) == {"command", "argv_shape", "verified_by"}
        assert entry["argv_shape"][0] == entry["command"]
    assert len(contract["contract_hash"]) == 64


def test_describe_cli_contract_is_byte_stable():
    assert canonical_bytes(describe_cli_contract()) == canonical_bytes(describe_cli_contract())


# --- the CLI-driven fixture bundle ---------------------------------------------


def test_build_cli_command_fixtures_covers_every_contracted_cli_surface():
    fixture = build_cli_command_fixtures()
    assert fixture["version"] == CLI_CONTRACT_VERSION
    assert set(fixture["artifacts"]) == set(CLI_COMMAND_CONTRACTS)
    assert len(fixture["bundle_hash"]) == 64


def test_build_cli_command_fixtures_is_byte_stable():
    fixture_a = build_cli_command_fixtures()
    fixture_b = build_cli_command_fixtures()
    assert canonical_bytes(fixture_a) == canonical_bytes(fixture_b)


def test_build_cli_command_fixtures_produces_artifacts_that_pass_their_own_verifiers():
    from screen_prove.extraction import verify_extraction_record
    from screen_prove.risk_of_bias import verify_rob_assessment
    from screen_prove.synthesis import verify_calculation

    fixture = build_cli_command_fixtures()
    artifacts = fixture["artifacts"]
    verify_extraction_record(artifacts["extraction_record"])
    verify_rob_assessment(artifacts["risk_of_bias_assessment"])
    verify_calculation(artifacts["synthesis_calculation"])


def test_verify_cli_command_fixtures_accepts_a_fresh_fixture():
    fixture = build_cli_command_fixtures()
    body = verify_cli_command_fixtures(fixture)
    assert body["version"] == CLI_CONTRACT_VERSION


def test_verify_cli_command_fixtures_rejects_a_tampered_bundle_hash():
    fixture = dict(build_cli_command_fixtures())
    fixture["bundle_hash"] = "0" * 64
    try:
        verify_cli_command_fixtures(fixture)
    except IntegrityError:
        pass
    else:
        raise AssertionError("tampered bundle_hash was not detected")


def test_verify_cli_command_fixtures_rejects_an_unsupported_version():
    fixture = build_cli_command_fixtures()
    body = {"version": "cli-contract-v0", "artifacts": fixture["artifacts"]}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_cli_command_fixtures(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("an unsupported CLI contract version was not rejected")


def test_verify_cli_command_fixtures_rejects_a_missing_artifact():
    fixture = build_cli_command_fixtures()
    artifacts = dict(fixture["artifacts"])
    del artifacts["synthesis_calculation"]
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_cli_command_fixtures(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a missing contracted CLI surface was not detected")


def test_verify_cli_command_fixtures_rejects_a_tampered_extraction_record():
    fixture = build_cli_command_fixtures()
    artifacts = dict(fixture["artifacts"])
    record = dict(artifacts["extraction_record"])
    record["fields"] = [{**record["fields"][0], "value": "9999"}]
    artifacts["extraction_record"] = record
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_cli_command_fixtures(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered extraction_record was not detected")


def test_verify_cli_command_fixtures_rejects_a_tampered_rob_assessment():
    fixture = build_cli_command_fixtures()
    artifacts = dict(fixture["artifacts"])
    rob = dict(artifacts["risk_of_bias_assessment"])
    rob["domains"] = [{**rob["domains"][0], "judgment": "high_risk"}]
    artifacts["risk_of_bias_assessment"] = rob
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_cli_command_fixtures(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered risk_of_bias_assessment was not detected")


def test_verify_cli_command_fixtures_rejects_a_tampered_synthesis_calculation():
    fixture = build_cli_command_fixtures()
    artifacts = dict(fixture["artifacts"])
    artifacts["synthesis_calculation"] = {**artifacts["synthesis_calculation"], "point_estimate": 0.99}
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_cli_command_fixtures(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered synthesis_calculation was not detected")


# --- build_cli_contract_receipt aggregation ------------------------------------


def test_build_cli_contract_receipt_passes_and_is_byte_stable():
    receipt_a = build_cli_contract_receipt()
    receipt_b = build_cli_contract_receipt()
    assert receipt_a["version"] == CLI_CONTRACT_RECEIPT_VERSION
    assert receipt_a["passed"] is True
    assert canonical_bytes(receipt_a) == canonical_bytes(receipt_b)


def test_build_cli_contract_receipt_fails_closed_when_a_subcommand_exits_non_zero(monkeypatch):
    import screen_prove.cli_contract as cli_contract

    def failing_main(argv):
        return 1

    monkeypatch.setattr(cli_contract, "cli_main", failing_main)
    receipt = build_cli_contract_receipt()
    assert receipt["passed"] is False
    assert "exited" in receipt["detail"]


# --- CLI integration -------------------------------------------------------------


def test_cli_cli_contract_writes_report_and_exits_zero(tmp_path):
    report_path = tmp_path / "cli-contract-report.json"
    exit_code = main(["cli-contract", "--output", str(report_path)])
    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["version"] == CLI_CONTRACT_RECEIPT_VERSION


def test_cli_cli_contract_prints_report_without_an_output_path(capsys):
    exit_code = main(["cli-contract"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
