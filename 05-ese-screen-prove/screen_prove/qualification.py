"""Offline qualification receipt: end-to-end evidence that every completed
engine stage works from fixed, offline fixtures, without network access.

`build_qualification_receipt` aggregates the repository readiness doctor
report (`screen_prove.doctor.run_doctor`, stage 1 of the work-package queue
in `docs/AUTONOMOUS_BUILD_LOOP.md`) with a bounded set of stage replay/tamper
checks, one per remaining completed governed stage: the live-provider/
connector boundary's default-deny gate (stage 2), the bounded public-canary
fixtures (stage 3), frozen extraction (stage 4), risk-of-bias (stage 5),
synthesis calculation (stage 6), the screen/package/replay chain built ahead
of the formal stage queue, and the extract/rob/synthesize CLI command
surfaces exercised through `screen_prove.cli.main` itself
(`screen_prove.cli_contract`, stage 10).

Each stage check builds an artifact from a small, fixed fixture, verifies it
round-trips through that stage's own `build_*`/`verify_*` functions (or, for
screening, the `screen_records`/`build_package`/`write_package`/
`verify_replay` chain; for the live boundary, the opt-in gate functions and
subclassing contract; for public canaries, each of the three canaries' own
`describe_query`/`describe_request` and `verify_response`), and then confirms
that tampering (or, for the boundary/canary stages, an unauthorized or
bypassing attempt, or an invalid/mismatched canary response) is
detected -- never silently accepted. If a stage's tamper detection ever
regresses (an altered artifact, or an unauthorized/bypassing attempt, is
accepted instead of rejected), the corresponding check reports
`passed: False` with an explicit detail rather than letting the regression
pass unnoticed. Every check that builds a hashable artifact also records that
artifact's own concrete hash (`record_hash`, `rob_hash`, `calculation_hash`,
`package_hash`, or a task-envelope/contract hash) in its `artifact_hash`
field, so the receipt substantiates *which* artifact each check verified, not
just a pass/fail label.

No check reads a live credential, opens a network socket, or calls a live
provider/connector; every fixture is fixed and defined in this module.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canaries import (
    CLINICALTRIALS_CANARY_NCT_ID,
    LLM_CANARY_RECORD_ID,
    LLM_CANARY_TITLE,
    PUBMED_CANARY_PMID,
    ClinicalTrialsRecordCanary,
    PubMedArticleCanary,
    SingleLlmScreeningCanary,
    build_task_envelope,
)
from .contracts import IntegrityError, ProtocolLock, object_hash, read_json, sha256_bytes, write_json
from .doctor import TestRunner, _default_test_runner, run_doctor
from .extraction import build_extraction_record, extract_field, verify_extraction_record
from .live_boundary import (
    LIVE_SMOKE_OPT_IN_ENV_VAR,
    ConnectorBoundary,
    LiveBoundaryAdapter,
    LiveExecutionNotAuthorized,
    ProviderBoundary,
    live_smoke_authorized,
    require_live_smoke_authorization,
)
from .replay import verify_replay, write_package
from .risk_of_bias import build_domain_judgment, build_rob_assessment, verify_rob_assessment
from .screening import StaticDecisionAdapter, build_package, screen_records
from .synthesis import build_analysis_recipe, calculate_effect_measure, verify_calculation

QUALIFICATION_RECEIPT_VERSION = "qualification-receipt-v1"


@dataclass(frozen=True)
class StageCheckResult:
    """One qualification stage check's outcome, including its artifact hash.

    `artifact_hash` is the concrete hash of the fixture artifact this check
    built and verified (for example a `record_hash` or `package_hash`), or
    the empty string when the check failed before an artifact could be built.
    """

    name: str
    passed: bool
    detail: str
    artifact_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "artifact_hash": self.artifact_hash,
        }


def check_extraction_replay() -> StageCheckResult:
    """Build, verify, and tamper-check one frozen-extraction fixture record."""
    full_text = "Sample size was 240 participants."
    source_hash = sha256_bytes(full_text.encode("utf-8"))
    provenance_file = "qualification-fixture-fulltext.txt"
    try:
        field = extract_field(full_text, source_hash, provenance_file, "sample_size", 16, 19)
        record = build_extraction_record("qualification:extraction:1", source_hash, provenance_file, [field])
        record_hash = record["record_hash"]
        verify_extraction_record(record)
        tampered = dict(record)
        tampered["fields"] = [dict(record["fields"][0])]
        tampered["fields"][0]["value"] = "9999"
        try:
            verify_extraction_record(tampered)
        except IntegrityError:
            pass
        else:
            return StageCheckResult("extraction_replay", False, "tampering was not detected", record_hash)
    except IntegrityError as exc:
        return StageCheckResult("extraction_replay", False, f"extraction replay failed: {exc}", "")
    return StageCheckResult(
        "extraction_replay",
        True,
        "extraction build/verify round-trip and tamper detection succeeded",
        record_hash,
    )


def check_risk_of_bias_replay() -> StageCheckResult:
    """Build, verify, and tamper-check one risk-of-bias fixture assessment."""
    try:
        domain = build_domain_judgment(
            "randomization_process", "low_risk", "computer-generated sequence reported", ["Methods p.2"]
        )
        assessment = build_rob_assessment("qualification:rob:1", "protocol-rob-v1", [domain], "low_risk")
        rob_hash = assessment["rob_hash"]
        verify_rob_assessment(assessment)
        tampered = dict(assessment)
        tampered["domains"] = [dict(assessment["domains"][0])]
        tampered["domains"][0]["judgment"] = "high_risk"
        try:
            verify_rob_assessment(tampered)
        except IntegrityError:
            pass
        else:
            return StageCheckResult("risk_of_bias_replay", False, "tampering was not detected", rob_hash)
    except IntegrityError as exc:
        return StageCheckResult("risk_of_bias_replay", False, f"risk-of-bias replay failed: {exc}", "")
    return StageCheckResult(
        "risk_of_bias_replay",
        True,
        "risk-of-bias build/verify round-trip and tamper detection succeeded",
        rob_hash,
    )


def check_synthesis_replay() -> StageCheckResult:
    """Calculate, verify, and tamper-check one synthesis-calculation fixture."""
    try:
        recipe = build_analysis_recipe("qualification:synthesis:1", "risk_difference")
        counts = {"treated_events": 30, "treated_total": 150, "control_events": 15, "control_total": 150}
        calculation = calculate_effect_measure(recipe, counts)
        calculation_hash = calculation["calculation_hash"]
        verify_calculation(calculation)
        tampered = dict(calculation)
        tampered["point_estimate"] = 0.5
        try:
            verify_calculation(tampered)
        except IntegrityError:
            pass
        else:
            return StageCheckResult("synthesis_replay", False, "tampering was not detected", calculation_hash)
    except IntegrityError as exc:
        return StageCheckResult("synthesis_replay", False, f"synthesis replay failed: {exc}", "")
    return StageCheckResult(
        "synthesis_replay",
        True,
        "synthesis build/verify round-trip and tamper detection succeeded",
        calculation_hash,
    )


def check_screening_end_to_end_replay() -> StageCheckResult:
    """Run one screen/package/replay fixture chain end to end and tamper-check it."""
    try:
        with tempfile.TemporaryDirectory(prefix="screen-prove-qualify-") as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            records = [{"record_id": "qualification:record:1", "title": "Fixture study"}]
            dedup = {
                "version": "dedup-v1",
                "source_hash": "a" * 64,
                "corpus_hash": "b" * 64,
                "records": records,
                "records_hash": object_hash({"records": records}),
                "decisions": [],
                "counts": {"identified": 1, "after_dedup": 1, "duplicates_removed": 0},
            }
            lock = ProtocolLock(
                {"eligibility": ["RCT"]}, {"steps": ["screen"]}, {"decision": "enum"}, dedup["corpus_hash"]
            )
            adapter = StaticDecisionAdapter({"qualification:record:1": "include"})
            screening = screen_records(dedup, lock, adapter, cache_dir, human_adjudication="off")
            package = build_package(screening)
            package_path = tmp_path / "package.json"
            write_package(package_path, package)
            package_hash = read_json(package_path)["package_hash"]
            verify_replay(package_path, lock, dedup, cache_dir)

            tampered = read_json(package_path)
            tampered["results"][0]["decision"] = "exclude"
            write_json(package_path, tampered)
            try:
                verify_replay(package_path, lock, dedup, cache_dir)
            except IntegrityError:
                pass
            else:
                return StageCheckResult(
                    "screening_end_to_end_replay", False, "tampering was not detected", package_hash
                )
    except IntegrityError as exc:
        return StageCheckResult(
            "screening_end_to_end_replay", False, f"screening end-to-end replay failed: {exc}", ""
        )
    return StageCheckResult(
        "screening_end_to_end_replay",
        True,
        "screen/package/replay round-trip and tamper detection succeeded",
        package_hash,
    )


def check_live_boundary_default_denial() -> StageCheckResult:
    """Verify stage 2 (`docs/AUTONOMOUS_BUILD_LOOP.md`): the live-provider/
    connector boundary defaults to deny and cannot be bypassed by a subclass.

    Exercises `screen_prove.live_boundary` with fixed, explicit environment
    mappings only -- it never reads the real process environment -- so the
    result never depends on ambient state. Confirms the opt-in gate denies a
    missing flag and every near-miss spelling, authorizes only the exact
    opt-in value, and that a subclass attempting to override the gate-
    enforcing `execute_live`/`fetch_live` entry points is rejected at
    class-definition time rather than silently allowed to bypass the gate.
    """
    name = "live_boundary_default_denial"
    contract = {"gate_env_var": LIVE_SMOKE_OPT_IN_ENV_VAR, "authorized_value": "1"}
    contract_hash = object_hash(contract)

    if live_smoke_authorized({}):
        return StageCheckResult(name, False, "absent opt-in was treated as authorized", contract_hash)
    try:
        require_live_smoke_authorization({})
    except LiveExecutionNotAuthorized:
        pass
    else:
        return StageCheckResult(name, False, "missing opt-in did not raise", contract_hash)

    for near_miss in ("0", "true", "TRUE", "yes", "on", " 1", "1 ", ""):
        if live_smoke_authorized({LIVE_SMOKE_OPT_IN_ENV_VAR: near_miss}):
            return StageCheckResult(
                name, False, f"near-miss opt-in value {near_miss!r} was authorized", contract_hash
            )

    if not live_smoke_authorized({LIVE_SMOKE_OPT_IN_ENV_VAR: "1"}):
        return StageCheckResult(name, False, "exact opt-in value was not authorized", contract_hash)
    try:
        require_live_smoke_authorization({LIVE_SMOKE_OPT_IN_ENV_VAR: "1"})
    except LiveExecutionNotAuthorized:
        return StageCheckResult(name, False, "exact opt-in value was denied", contract_hash)

    try:
        class _QualificationBypassAdapter(LiveBoundaryAdapter):
            def execute_live(self, request):  # pragma: no cover - must never be reached
                return {"decision": "include", "reason": "gate bypassed"}
    except TypeError:
        pass
    else:
        return StageCheckResult(name, False, "a gate-bypassing subclass was not rejected", contract_hash)

    return StageCheckResult(
        name,
        True,
        "live-provider/connector boundary defaults to deny and rejects a bypass subclass",
        contract_hash,
    )


def check_public_canary_fixture_validation() -> StageCheckResult:
    """Verify stage 3 (`docs/AUTONOMOUS_BUILD_LOOP.md`): all three bounded
    public-canary fixtures -- PubMed and ClinicalTrials.gov (both
    `ConnectorBoundary`) and the single-LLM screening canary
    (`ProviderBoundary`) -- have byte-stable offline request descriptions and
    task envelopes, and each rejects both a mismatched canary identity and a
    disallowed/invalid response rather than silently accepting it. No network
    call is made or attempted; the fixed `PUBMED_CANARY_PMID`,
    `CLINICALTRIALS_CANARY_NCT_ID`, `LLM_CANARY_RECORD_ID` and a supplied
    fixture response are the only inputs for each canary.
    """
    name = "public_canary_fixture_validation"
    lock = ProtocolLock({"eligibility": ["RCT"]}, {"steps": ["screen"]}, {"decision": "enum"}, "a" * 64)

    try:
        # --- PubMed article connector canary ---
        pubmed_canary = PubMedArticleCanary()
        if not isinstance(pubmed_canary, ConnectorBoundary):
            return StageCheckResult(name, False, "PubMed canary is not a ConnectorBoundary", "")

        pubmed_description = pubmed_canary.describe_query({"pmid": PUBMED_CANARY_PMID})
        pubmed_response = {"pmid": PUBMED_CANARY_PMID, "access_state": "abstract_available"}
        pubmed_canary.verify_response({"pmid": PUBMED_CANARY_PMID}, pubmed_response)
        pubmed_envelope = build_task_envelope(
            lock, pubmed_description, pubmed_canary.source, "canary-fixture", pubmed_response
        )
        pubmed_envelope_hash = object_hash(pubmed_envelope.to_dict())

        pubmed_mismatched_identity = {"pmid": "00000000", "access_state": "abstract_available"}
        try:
            pubmed_canary.verify_response({"pmid": PUBMED_CANARY_PMID}, pubmed_mismatched_identity)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(
                name, False, "a mismatched PubMed canary identity was not detected", pubmed_envelope_hash
            )

        pubmed_disallowed_access_state = {"pmid": PUBMED_CANARY_PMID, "access_state": "login_required"}
        try:
            pubmed_canary.verify_response({"pmid": PUBMED_CANARY_PMID}, pubmed_disallowed_access_state)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(
                name, False, "a disallowed PubMed canary access state was not detected", pubmed_envelope_hash
            )

        # --- ClinicalTrials.gov record connector canary ---
        ctgov_canary = ClinicalTrialsRecordCanary()
        if not isinstance(ctgov_canary, ConnectorBoundary):
            return StageCheckResult(
                name, False, "ClinicalTrials.gov canary is not a ConnectorBoundary", pubmed_envelope_hash
            )

        ctgov_description = ctgov_canary.describe_query({"nct_id": CLINICALTRIALS_CANARY_NCT_ID})
        ctgov_response = {"nct_id": CLINICALTRIALS_CANARY_NCT_ID, "access_state": "metadata_only"}
        ctgov_canary.verify_response({"nct_id": CLINICALTRIALS_CANARY_NCT_ID}, ctgov_response)
        ctgov_envelope = build_task_envelope(
            lock, ctgov_description, ctgov_canary.source, "canary-fixture", ctgov_response
        )
        ctgov_envelope_hash = object_hash(ctgov_envelope.to_dict())

        ctgov_mismatched_identity = {"nct_id": "NCT99999999", "access_state": "metadata_only"}
        try:
            ctgov_canary.verify_response({"nct_id": CLINICALTRIALS_CANARY_NCT_ID}, ctgov_mismatched_identity)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(
                name,
                False,
                "a mismatched ClinicalTrials.gov canary identity was not detected",
                ctgov_envelope_hash,
            )

        ctgov_disallowed_access_state = {"nct_id": CLINICALTRIALS_CANARY_NCT_ID, "access_state": "paywalled"}
        try:
            ctgov_canary.verify_response({"nct_id": CLINICALTRIALS_CANARY_NCT_ID}, ctgov_disallowed_access_state)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(
                name,
                False,
                "a disallowed ClinicalTrials.gov canary access state was not detected",
                ctgov_envelope_hash,
            )

        # --- Single-LLM screening provider canary ---
        llm_canary = SingleLlmScreeningCanary()
        if not isinstance(llm_canary, ProviderBoundary):
            return StageCheckResult(
                name, False, "LLM screening canary is not a ProviderBoundary", ctgov_envelope_hash
            )

        llm_record = {"record_id": LLM_CANARY_RECORD_ID, "title": LLM_CANARY_TITLE}
        llm_description = llm_canary.describe_request(llm_record)
        llm_response = {"record_id": LLM_CANARY_RECORD_ID, "decision": "include"}
        llm_canary.verify_response(llm_record, llm_response)
        llm_envelope = build_task_envelope(lock, llm_description, llm_canary.provider, llm_canary.model, llm_response)
        llm_envelope_hash = object_hash(llm_envelope.to_dict())

        llm_mismatched_identity = {"record_id": "not-the-fixture-record", "decision": "include"}
        try:
            llm_canary.verify_response(llm_record, llm_mismatched_identity)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(
                name, False, "a mismatched LLM canary identity was not detected", llm_envelope_hash
            )

        llm_invalid_decision = {"record_id": LLM_CANARY_RECORD_ID, "decision": "maybe"}
        try:
            llm_canary.verify_response(llm_record, llm_invalid_decision)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(
                name, False, "an invalid LLM canary decision was not detected", llm_envelope_hash
            )
    except IntegrityError as exc:
        return StageCheckResult(name, False, f"public canary fixture validation failed: {exc}", "")

    combined_hash = object_hash(
        {
            "pubmed_envelope_hash": pubmed_envelope_hash,
            "clinicaltrials_envelope_hash": ctgov_envelope_hash,
            "llm_envelope_hash": llm_envelope_hash,
        }
    )
    return StageCheckResult(
        name,
        True,
        "public PubMed, ClinicalTrials.gov, and LLM-screening canary fixture "
        "build/verify and tamper detection succeeded",
        combined_hash,
    )


def check_cli_command_surface_replay() -> StageCheckResult:
    """Verify stage 10 (`docs/AUTONOMOUS_BUILD_LOOP.md`): the `extract`,
    `rob`, and `synthesize` run-level CLI surfaces build and verify real
    artifacts when driven through `screen_prove.cli.main` itself, not just
    their underlying engine functions.

    Imports `screen_prove.cli_contract` lazily (inside this function, not at
    module import time) because that module imports `screen_prove.cli.main`
    to drive the subcommands it exercises, and `screen_prove.cli` imports
    this `qualification` module at CLI-startup time -- a module-level import
    here would be circular.
    """
    from .cli_contract import (
        CLI_CONTRACT_VERSION,
        build_cli_command_fixtures,
        verify_cli_command_fixtures,
    )

    name = "cli_command_surface_replay"
    try:
        fixture = build_cli_command_fixtures()
        bundle_hash = fixture["bundle_hash"]
        verify_cli_command_fixtures(fixture)
        artifacts = dict(fixture["artifacts"])
        record = dict(artifacts["extraction_record"])
        record["fields"] = [dict(record["fields"][0])]
        record["fields"][0]["value"] = "9999"
        artifacts["extraction_record"] = record
        tampered_body = {"version": CLI_CONTRACT_VERSION, "artifacts": artifacts}
        tampered = {**tampered_body, "bundle_hash": object_hash(tampered_body)}
        try:
            verify_cli_command_fixtures(tampered)
        except IntegrityError:
            pass
        else:
            return StageCheckResult(name, False, "tampering was not detected", bundle_hash)
    except IntegrityError as exc:
        return StageCheckResult(name, False, f"CLI command surface replay failed: {exc}", "")
    return StageCheckResult(
        name,
        True,
        "extract/rob/synthesize CLI command fixtures built through screen_prove.cli.main, "
        "verified, and tamper detection succeeded",
        bundle_hash,
    )


def build_qualification_receipt(
    repo_root: Path,
    *,
    skip_tests: bool = False,
    test_runner: TestRunner = _default_test_runner,
) -> dict[str, Any]:
    """Build a byte-stable qualification receipt covering every completed stage.

    Aggregates the repository readiness doctor report (stage 1) with
    fixture-only replay/tamper checks for every other completed governed
    engine stage from `docs/AUTONOMOUS_BUILD_LOOP.md`: the live-boundary
    default-deny gate (stage 2), the public-canary fixtures (stage 3), frozen
    extraction (stage 4), risk-of-bias (stage 5), synthesis calculation
    (stage 6), the screen/package/replay chain built ahead of the formal
    stage queue, and the extract/rob/synthesize CLI command surfaces
    exercised through `screen_prove.cli.main` itself (stage 10). Every check
    is offline: no network access, no live credentials are read, and
    `repo_root` is never modified. Each stage check also records the
    concrete hash of the artifact it built and verified. `passed` is True
    only if the doctor report and every stage check passed.
    """
    doctor_report = run_doctor(Path(repo_root), skip_tests=skip_tests, test_runner=test_runner)
    stage_checks = (
        check_live_boundary_default_denial(),
        check_public_canary_fixture_validation(),
        check_extraction_replay(),
        check_risk_of_bias_replay(),
        check_synthesis_replay(),
        check_screening_end_to_end_replay(),
        check_cli_command_surface_replay(),
    )
    passed = doctor_report.passed and all(check.passed for check in stage_checks)
    body = {
        "version": QUALIFICATION_RECEIPT_VERSION,
        "doctor": doctor_report.to_dict(),
        "stage_checks": [check.to_dict() for check in stage_checks],
        "passed": passed,
    }
    return {**body, "qualification_hash": object_hash(body)}
