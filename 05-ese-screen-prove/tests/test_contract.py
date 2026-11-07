import json

from screen_prove.cli import main
from screen_prove.contracts import IntegrityError, canonical_bytes
from screen_prove.contract import (
    ARTIFACT_CONTRACTS,
    CONTRACT_RECEIPT_VERSION,
    ENGINE_CONTRACT_VERSION,
    build_compatibility_fixture,
    build_contract_receipt,
    describe_engine_contract,
    verify_compatibility_fixture,
    verify_deduplicated_corpus,
    verify_frozen_corpus,
)


# --- the documented contract itself -------------------------------------------


def test_describe_engine_contract_lists_every_expected_artifact():
    contract = describe_engine_contract()
    assert contract["version"] == ENGINE_CONTRACT_VERSION
    assert set(contract["artifacts"]) == {
        "protocol_lock",
        "frozen_corpus",
        "deduplicated_corpus",
        "screening_package",
        "extraction_record",
        "risk_of_bias_assessment",
        "synthesis_calculation",
    }
    for entry in contract["artifacts"].values():
        assert set(entry) == {"cli_command", "built_by", "verified_by"}
    assert len(contract["contract_hash"]) == 64


def test_describe_engine_contract_documents_the_extract_rob_synthesize_cli_commands():
    # Stage 10 (`docs/AUTONOMOUS_BUILD_LOOP.md`): the extraction/risk-of-bias/
    # synthesis artifacts each have a dedicated `screen-prove` subcommand,
    # not an empty placeholder -- see `screen_prove.cli_contract` for the
    # CLI-driven fixture that exercises these commands end to end.
    contract = describe_engine_contract()
    assert contract["artifacts"]["extraction_record"]["cli_command"] == "extract"
    assert contract["artifacts"]["risk_of_bias_assessment"]["cli_command"] == "rob"
    assert contract["artifacts"]["synthesis_calculation"]["cli_command"] == "synthesize"


def test_describe_engine_contract_is_byte_stable():
    assert canonical_bytes(describe_engine_contract()) == canonical_bytes(describe_engine_contract())


# --- the compatibility fixture bundle -----------------------------------------


def test_build_compatibility_fixture_covers_every_contracted_artifact():
    fixture = build_compatibility_fixture()
    assert fixture["version"] == ENGINE_CONTRACT_VERSION
    assert set(fixture["artifacts"]) == set(ARTIFACT_CONTRACTS)
    assert len(fixture["bundle_hash"]) == 64


def test_build_compatibility_fixture_is_byte_stable():
    fixture_a = build_compatibility_fixture()
    fixture_b = build_compatibility_fixture()
    assert canonical_bytes(fixture_a) == canonical_bytes(fixture_b)


def test_build_compatibility_fixture_traces_one_record_across_every_artifact():
    fixture = build_compatibility_fixture()
    artifacts = fixture["artifacts"]
    record_id = artifacts["deduplicated_corpus"]["records"][0]["record_id"]
    assert artifacts["screening_package"]["results"][0]["record_id"] == record_id
    assert artifacts["extraction_record"]["record_id"] == record_id
    assert artifacts["risk_of_bias_assessment"]["record_id"] == record_id
    assert artifacts["synthesis_calculation"]["record_id"] == record_id


def test_verify_compatibility_fixture_accepts_a_fresh_fixture():
    fixture = build_compatibility_fixture()
    body = verify_compatibility_fixture(fixture)
    assert body["version"] == ENGINE_CONTRACT_VERSION


def test_verify_compatibility_fixture_rejects_a_tampered_bundle_hash():
    fixture = dict(build_compatibility_fixture())
    fixture["bundle_hash"] = "0" * 64
    try:
        verify_compatibility_fixture(fixture)
    except IntegrityError:
        pass
    else:
        raise AssertionError("tampered bundle_hash was not detected")


def test_verify_compatibility_fixture_rejects_an_unsupported_version():
    fixture = build_compatibility_fixture()
    tampered = dict(fixture)
    tampered["version"] = "engine-contract-v0"
    tampered["bundle_hash"] = None
    body = {key: value for key, value in tampered.items() if key != "bundle_hash"}
    from screen_prove.contracts import object_hash

    tampered["bundle_hash"] = object_hash(body)
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("an unsupported contract version was not rejected")


def test_verify_compatibility_fixture_rejects_a_missing_artifact():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    del artifacts["synthesis_calculation"]
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a missing contracted artifact was not detected")


def test_verify_compatibility_fixture_rejects_a_tampered_protocol_lock():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    artifacts["protocol_lock"] = {**artifacts["protocol_lock"], "protocol_hash": "0" * 64}
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered protocol_lock was not detected")


def test_verify_compatibility_fixture_rejects_a_deduplicated_corpus_mismatch():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    dedup = dict(artifacts["deduplicated_corpus"])
    dedup["records"] = []
    artifacts["deduplicated_corpus"] = dedup
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a deduplicated_corpus that no longer matches frozen_corpus was not detected")


def test_verify_compatibility_fixture_rejects_a_tampered_screening_package():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    package = dict(artifacts["screening_package"])
    package["results"] = [{**row, "decision": "exclude"} for row in package["results"]]
    artifacts["screening_package"] = package
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered screening_package was not detected")


def test_verify_compatibility_fixture_rejects_a_tampered_extraction_record():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    record = dict(artifacts["extraction_record"])
    record["fields"] = [{**record["fields"][0], "value": "9999"}]
    artifacts["extraction_record"] = record
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered extraction_record was not detected")


def test_verify_compatibility_fixture_rejects_a_tampered_risk_of_bias_assessment():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    rob = dict(artifacts["risk_of_bias_assessment"])
    rob["domains"] = [{**rob["domains"][0], "judgment": "high_risk"}]
    artifacts["risk_of_bias_assessment"] = rob
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered risk_of_bias_assessment was not detected")


def test_verify_compatibility_fixture_rejects_a_tampered_synthesis_calculation():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    artifacts["synthesis_calculation"] = {**artifacts["synthesis_calculation"], "point_estimate": 0.99}
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered synthesis_calculation was not detected")


def test_verify_compatibility_fixture_rejects_a_screening_package_for_an_unrelated_protocol_lock():
    from screen_prove.contracts import object_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    package = dict(artifacts["screening_package"])
    package_body = {key: value for key, value in package.items() if key != "package_hash"}
    # Rehash so the substituted package is internally self-consistent (its own
    # package_hash matches its own content, and would pass `_verify_package_artifact`
    # in isolation) even though its manifest no longer references this bundle's
    # protocol_lock.
    package_body["manifest"] = {**package_body["manifest"], "lock_hash": "0" * 64}
    unrelated_package = {**package_body, "package_hash": object_hash(package_body)}
    artifacts["screening_package"] = unrelated_package
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError(
            "an internally valid screening_package referencing an unrelated protocol_lock was accepted"
        )


def test_verify_compatibility_fixture_rejects_an_extraction_record_for_an_unrelated_record():
    from screen_prove.contracts import object_hash, sha256_bytes
    from screen_prove.extraction import build_extraction_record, extract_field, verify_extraction_record

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])

    unrelated_text = "Unrelated study text."
    unrelated_source_hash = sha256_bytes(unrelated_text.encode("utf-8"))
    unrelated_field = extract_field(
        unrelated_text, unrelated_source_hash, "unrelated-fulltext.txt", "sample_size", 0, 9
    )
    unrelated_extraction = build_extraction_record(
        "unrelated-record-id", unrelated_source_hash, "unrelated-fulltext.txt", [unrelated_field]
    )
    # Sanity: the substituted artifact is internally valid entirely on its own.
    verify_extraction_record(unrelated_extraction)

    artifacts["extraction_record"] = unrelated_extraction
    body = {"version": fixture["version"], "artifacts": artifacts}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError(
            "an internally valid extraction_record for an unrelated record was accepted"
        )


# --- artifact-only verifiers (`ARTIFACT_CONTRACTS`' `verified_by` entries) ----


def test_verify_frozen_corpus_accepts_a_fresh_frozen_corpus():
    fixture = build_compatibility_fixture()
    corpus = fixture["artifacts"]["frozen_corpus"]
    # Single-argument call, exactly the invocation ARTIFACT_CONTRACTS documents.
    verified = verify_frozen_corpus(corpus)
    assert verified["corpus_hash"] == corpus["corpus_hash"]


def test_verify_frozen_corpus_rejects_a_tampered_frozen_corpus():
    fixture = build_compatibility_fixture()
    corpus = dict(fixture["artifacts"]["frozen_corpus"])
    corpus["records"] = []
    try:
        verify_frozen_corpus(corpus)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered frozen_corpus was not detected")


def test_verify_deduplicated_corpus_accepts_a_fresh_deduplicated_corpus():
    fixture = build_compatibility_fixture()
    dedup = fixture["artifacts"]["deduplicated_corpus"]
    # Single-argument call, exactly the invocation ARTIFACT_CONTRACTS documents
    # -- no protocol lock, adapter, cache directory, or adjudication mode.
    verified = verify_deduplicated_corpus(dedup)
    assert verified["records_hash"] == dedup["records_hash"]


def test_verify_deduplicated_corpus_rejects_a_tampered_deduplicated_corpus():
    fixture = build_compatibility_fixture()
    dedup = dict(fixture["artifacts"]["deduplicated_corpus"])
    dedup["records"] = []
    try:
        verify_deduplicated_corpus(dedup)
    except IntegrityError:
        pass
    else:
        raise AssertionError("a tampered deduplicated_corpus was not detected")


# --- replay_cache: what pairing screening_package with the bundle's own ------
# --- cache evidence does, and does not, catch (see the module docstring) -----


def test_verify_compatibility_fixture_rejects_a_replay_cache_only_tamper():
    from screen_prove.contracts import object_hash
    from screen_prove.screening import _response_hash

    fixture = build_compatibility_fixture()
    replay_cache = dict(fixture["replay_cache"])
    request_hash = next(iter(replay_cache))
    tampered_response = {"decision": "exclude", "reason": "tampered cache entry"}
    replay_cache[request_hash] = {
        **replay_cache[request_hash],
        "response": tampered_response,
        "response_hash": _response_hash(tampered_response),
    }
    body = {"version": fixture["version"], "artifacts": fixture["artifacts"], "replay_cache": replay_cache}
    tampered = {**body, "bundle_hash": object_hash(body)}
    try:
        verify_compatibility_fixture(tampered)
    except IntegrityError:
        pass
    else:
        raise AssertionError(
            "a replay_cache entry tampered without a matching screening_package edit was accepted"
        )


def test_verify_compatibility_fixture_does_not_detect_a_paired_package_and_cache_tamper():
    """Characterizes the documented boundary of this bundle format (see the
    module docstring and `_verify_screening_replay`): `replay_cache` is
    preserved *inside the same self-hashed bundle* as `screening_package`,
    not in an independent, out-of-band location. A caller able to rewrite
    the whole "at rest" bundle can edit `screening_package`'s decision and
    the matching `replay_cache` entry together, recompute every hash the
    edit touches (`package_hash` and `bundle_hash`), and this format cannot
    catch that coordinated edit -- unlike a genuinely separate `cache_dir`
    checked by a live `replay.verify_replay` call. This is expected,
    documented behavior, not a regression: it is why the module docstring
    narrows `verify_compatibility_fixture`'s guarantee to corruption /
    single-artifact tampering rather than authenticity.
    """
    from screen_prove.contracts import object_hash
    from screen_prove.screening import _response_hash

    fixture = build_compatibility_fixture()
    artifacts = dict(fixture["artifacts"])
    replay_cache = dict(fixture["replay_cache"])

    package = dict(artifacts["screening_package"])
    results = [dict(row) for row in package["results"]]
    assert len(results) == 1
    row = results[0]
    request_hash = row["envelope"]["request_hash"]
    assert request_hash in replay_cache
    assert row["decision"] == "include"

    tampered_response = {"decision": "exclude", "reason": "tampered decision"}
    row["decision"] = tampered_response["decision"]
    row["reason"] = tampered_response["reason"]
    row["envelope"] = {**row["envelope"], "response_hash": _response_hash(tampered_response)}
    package["results"] = [row]
    package_body = {key: value for key, value in package.items() if key != "package_hash"}
    artifacts["screening_package"] = {**package_body, "package_hash": object_hash(package_body)}

    replay_cache[request_hash] = {
        **replay_cache[request_hash],
        "response": tampered_response,
        "response_hash": _response_hash(tampered_response),
    }

    body = {"version": fixture["version"], "artifacts": artifacts, "replay_cache": replay_cache}
    tampered = {**body, "bundle_hash": object_hash(body)}

    verified = verify_compatibility_fixture(tampered)
    assert verified["artifacts"]["screening_package"]["results"][0]["decision"] == "exclude"


# --- build_contract_receipt aggregation ---------------------------------------


def test_build_contract_receipt_passes_and_is_byte_stable():
    receipt_a = build_contract_receipt()
    receipt_b = build_contract_receipt()
    assert receipt_a["version"] == CONTRACT_RECEIPT_VERSION
    assert receipt_a["passed"] is True
    assert canonical_bytes(receipt_a) == canonical_bytes(receipt_b)


# --- CLI integration -----------------------------------------------------------


def test_cli_contract_writes_report_and_exits_zero(tmp_path):
    report_path = tmp_path / "contract-report.json"
    exit_code = main(["contract", "--output", str(report_path)])
    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["version"] == CONTRACT_RECEIPT_VERSION


def test_cli_contract_prints_report_without_an_output_path(capsys):
    exit_code = main(["contract"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
