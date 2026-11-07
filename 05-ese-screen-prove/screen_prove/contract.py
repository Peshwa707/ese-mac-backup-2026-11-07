"""Engine/WebUI compatibility contract: stage 9 of the work-package queue in
`docs/AUTONOMOUS_BUILD_LOOP.md`.

This module lets an independent WebUI drive this engine using versioned
artifacts, without this engine repository editing or driving the WebUI
repository itself (see the WebUI note in `docs/AUTONOMOUS_BUILD_LOOP.md`'s
objective). It documents, in one place, every versioned artifact the engine
can hand to a consumer (`ARTIFACT_CONTRACTS`: which CLI subcommand or engine
function produces it, and which function re-verifies a stored copy before it
is trusted), and it builds/verifies one small, fixed, end-to-end
*compatibility fixture* bundle that exercises that whole documented chain --
protocol lock, frozen corpus, deduplicated corpus, screening package,
extraction record, risk-of-bias assessment, and synthesis calculation -- for
one traced record.

`build_compatibility_fixture` performs every intermediate build/verify step
itself (mirroring the pattern already used by `qualification.py` and
`validation.py`); the returned bundle only ever contains already-valid
artifacts, and no I/O outlives the function call except a scratch temporary
directory that is discarded before it returns. `verify_compatibility_fixture`
re-verifies an "at rest" bundle from nothing but its own content: it detects
accidental or partial corruption of the bundle itself, of any individual
contracted artifact (including a full `replay.verify_replay` of
`screening_package` against the bundle's own preserved `replay_cache`
evidence, not just a `package_hash` recomputation), of the internal
consistency between `frozen_corpus` and `deduplicated_corpus`, and of the
cross-artifact linkage that proves the bundle is one coherent end-to-end
chain -- not a set of individually valid artifacts stitched together from
unrelated records, protocol locks, or corpora.

Every one of those checks, like every other `_hash`/`bundle_hash` check in
this codebase, is a self-hash comparison: it proves the checked content is
*internally consistent with its own recorded hash*, not that the content was
authored by a trusted party. A caller who can rewrite the whole "at rest"
bundle can edit any artifact and then recompute every hash the edit
touches, including `bundle_hash` itself, and `verify_compatibility_fixture`
will accept the result -- this is true of `bundle_hash` as much as of
`package_hash`, `rob_hash`, or `record_hash`. In particular, `replay_cache`
is preserved *inside this same self-hashed bundle* as the `screening_package`
it replays against, so it is not independent, out-of-band evidence the way a
genuinely separate, access-controlled `cache_dir` is for a live
`replay.verify_replay` call: replaying `screening_package` against the
bundle's own `replay_cache` catches a `decision`, `reason`, or envelope hash
edited in *one* of those two artifacts without a matching edit to the other,
but it cannot catch a coordinated edit to both followed by a full hash
recomputation (see `_verify_screening_replay` and
`tests/test_contract.py::test_verify_compatibility_fixture_does_not_detect_a_paired_package_and_cache_tamper`
for the exact boundary this bundle format does and does not enforce).

No network access, no live credentials, and no new engine calculation
behavior is added here -- every artifact is built and re-verified through the
existing `build_*`/`verify_*` entry points of the already-completed stages.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    IntegrityError,
    ProtocolLock,
    _is_sha256,
    object_hash,
    read_json,
    sha256_bytes,
    write_json,
)
from .dedup import deduplicate
from .extraction import build_extraction_record, extract_field, verify_extraction_record
from .imports import freeze_import
from .replay import verify_replay, write_package
from .risk_of_bias import build_domain_judgment, build_rob_assessment, verify_rob_assessment
from .screening import StaticDecisionAdapter, build_package, screen_records
from .synthesis import build_analysis_recipe, calculate_effect_measure, verify_calculation

ENGINE_CONTRACT_VERSION = "engine-contract-v1"
CONTRACT_RECEIPT_VERSION = "engine-contract-receipt-v1"

# Every versioned artifact this offline engine can hand to an independent
# WebUI, keyed by a stable artifact name. `cli_command` names the exact
# `screen-prove` subcommand (see `screen_prove.cli`) that produces the
# artifact. Every artifact below now has a dedicated CLI subcommand -- the
# `extraction_record`, `risk_of_bias_assessment`, and `synthesis_calculation`
# CLI surfaces (`extract`/`rob`/`synthesize`) are additionally exercised
# through `screen_prove.cli.main` itself (not just their engine functions
# directly) by `screen_prove.cli_contract`, stage 10 of the work-package
# queue in `docs/AUTONOMOUS_BUILD_LOOP.md`; see that module for the
# CLI-driven fixture and command-shape contract. `built_by`
# and `verified_by` are module-qualified references, not importable
# callables. For every entry except `screening_package`, `verified_by`
# names an *artifact-only* verifier: a WebUI consumer re-verifies a stored
# artifact by calling that function with the artifact itself as its only
# argument (no file I/O, no additional context) before trusting its
# content. `frozen_corpus` and `deduplicated_corpus` are re-verified this
# way by the dedicated `verify_frozen_corpus`/`verify_deduplicated_corpus`
# functions below -- not by `dedup.deduplicate` or
# `screening.screen_records`, which build a *different*, downstream
# artifact rather than re-verify the one named here, and which (for
# `screen_records`) also require a protocol lock, adapter, cache
# directory, and adjudication mode the consumer would have to supply.
# `screening_package`'s `verified_by`, `replay.verify_replay`, is the one
# documented exception to the single-argument rule: it additionally
# requires this same bundle's `protocol_lock`, `deduplicated_corpus`, and
# preserved cache evidence, exactly as `_verify_screening_replay` below
# invokes it.
ARTIFACT_CONTRACTS: dict[str, dict[str, str]] = {
    "protocol_lock": {
        "cli_command": "lock",
        "built_by": "screen_prove.contracts.ProtocolLock",
        "verified_by": "screen_prove.contracts.ProtocolLock.from_dict",
    },
    "frozen_corpus": {
        "cli_command": "import",
        "built_by": "screen_prove.imports.freeze_import",
        "verified_by": "screen_prove.contract.verify_frozen_corpus",
    },
    "deduplicated_corpus": {
        "cli_command": "dedup",
        "built_by": "screen_prove.dedup.deduplicate",
        "verified_by": "screen_prove.contract.verify_deduplicated_corpus",
    },
    "screening_package": {
        "cli_command": "package",
        "built_by": "screen_prove.screening.build_package",
        "verified_by": "screen_prove.replay.verify_replay",
    },
    "extraction_record": {
        "cli_command": "extract",
        "built_by": "screen_prove.extraction.build_extraction_record",
        "verified_by": "screen_prove.extraction.verify_extraction_record",
    },
    "risk_of_bias_assessment": {
        "cli_command": "rob",
        "built_by": "screen_prove.risk_of_bias.build_rob_assessment",
        "verified_by": "screen_prove.risk_of_bias.verify_rob_assessment",
    },
    "synthesis_calculation": {
        "cli_command": "synthesize",
        "built_by": "screen_prove.synthesis.calculate_effect_measure",
        "verified_by": "screen_prove.synthesis.verify_calculation",
    },
}


_FROZEN_CORPUS_VERSION = "frozen-corpus-v1"
_FROZEN_CORPUS_REQUIRED_KEYS = ("version", "source_name", "source_file", "source_hash", "records")

_DEDUP_CORPUS_VERSION = "dedup-v1"
_DEDUP_CORPUS_REQUIRED_KEYS = (
    "version",
    "source_hash",
    "corpus_hash",
    "records",
    "records_hash",
    "decisions",
    "counts",
)


def verify_frozen_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Re-verify a stored `frozen_corpus` artifact from nothing but its own
    content -- the artifact-only verifier `ARTIFACT_CONTRACTS` documents
    for `frozen_corpus` (a single argument, no file I/O, no extra context).

    First confirms every field `imports.freeze_import` writes --
    `version`/`source_name`/`source_file`/`source_hash`/`records` -- is
    actually present (a payload that merely sets `corpus_hash =
    object_hash({})` and omits the rest is rejected here, before any hash
    comparison), that `version` is the supported `frozen-corpus-v1`, that
    `source_hash` is a well-formed SHA-256 digest, and that `records` is a
    list. Only then recomputes `corpus_hash` over exactly those five
    required fields, exactly as `imports.freeze_import` hashed them when it
    built the artifact (and the same check `dedup.deduplicate` performs as
    a precondition before it goes on to build an unrelated
    `deduplicated_corpus` artifact). Raises `IntegrityError` on any
    mismatch.
    """
    if not isinstance(corpus, Mapping):
        raise IntegrityError("frozen_corpus artifact must be an object")
    missing = [key for key in _FROZEN_CORPUS_REQUIRED_KEYS if key not in corpus]
    if missing:
        raise IntegrityError(
            f"frozen_corpus artifact is missing required field(s): {', '.join(missing)}"
        )
    if corpus.get("version") != _FROZEN_CORPUS_VERSION:
        raise IntegrityError(f"unsupported frozen_corpus version: {corpus.get('version')!r}")
    if not _is_sha256(str(corpus.get("source_hash"))):
        raise IntegrityError("frozen_corpus source_hash must be a SHA-256 hex digest")
    if not isinstance(corpus.get("records"), list):
        raise IntegrityError("frozen_corpus records must be a list")
    body = {key: corpus[key] for key in _FROZEN_CORPUS_REQUIRED_KEYS}
    if corpus.get("corpus_hash") != object_hash(body):
        raise IntegrityError("corpus_hash does not match frozen_corpus content")
    return dict(corpus)


def verify_deduplicated_corpus(dedup: Mapping[str, Any]) -> dict[str, Any]:
    """Re-verify a stored `deduplicated_corpus` artifact from nothing but its
    own content -- the artifact-only verifier `ARTIFACT_CONTRACTS`
    documents for `deduplicated_corpus` (a single argument, no file I/O, no
    extra context).

    First confirms every field `dedup.deduplicate` writes --
    `version`/`source_hash`/`corpus_hash`/`records`/`records_hash`/
    `decisions`/`counts` -- is actually present, that `version` is the
    supported `dedup-v1`, that `source_hash` and `corpus_hash` are
    well-formed SHA-256 digests, that `records` and `decisions` are lists
    whose entries are objects carrying a `record_id`, and that `counts`
    is an object whose `identified`/`after_dedup`/`duplicates_removed`
    fields actually match this artifact's own `records`/`decisions`
    lengths (`identified == after_dedup + duplicates_removed`). Only then
    recomputes `records_hash` over the artifact's own `records`, the same
    check `screening.screen_records` performs as a precondition before it
    goes on to build an unrelated `screening_package` artifact -- without
    also requiring `screen_records`'s protocol lock, adapter, cache
    directory, or adjudication mode. Raises `IntegrityError` on any
    mismatch. This does not, on its own, prove the artifact was correctly
    deduplicated *from* a given `frozen_corpus`;
    `verify_compatibility_fixture` checks that separately by recomputing
    `dedup.deduplicate(frozen_corpus)` and comparing.
    """
    if not isinstance(dedup, Mapping):
        raise IntegrityError("deduplicated_corpus artifact must be an object")
    missing = [key for key in _DEDUP_CORPUS_REQUIRED_KEYS if key not in dedup]
    if missing:
        raise IntegrityError(
            f"deduplicated_corpus artifact is missing required field(s): {', '.join(missing)}"
        )
    if dedup.get("version") != _DEDUP_CORPUS_VERSION:
        raise IntegrityError(f"unsupported deduplicated_corpus version: {dedup.get('version')!r}")
    if not _is_sha256(str(dedup.get("source_hash"))):
        raise IntegrityError("deduplicated_corpus source_hash must be a SHA-256 hex digest")
    if not _is_sha256(str(dedup.get("corpus_hash"))):
        raise IntegrityError("deduplicated_corpus corpus_hash must be a SHA-256 hex digest")
    records = dedup.get("records")
    decisions = dedup.get("decisions")
    if not isinstance(records, list) or not all(
        isinstance(record, Mapping) and "record_id" in record for record in records
    ):
        raise IntegrityError("deduplicated_corpus records must be a list of records with record_id")
    if not isinstance(decisions, list) or not all(
        isinstance(decision, Mapping)
        and {"record_id", "kept_record_id", "reason"} <= set(decision)
        for decision in decisions
    ):
        raise IntegrityError(
            "deduplicated_corpus decisions must be a list of "
            "record_id/kept_record_id/reason objects"
        )
    counts = dedup.get("counts")
    expected_counts = {
        "identified": len(records) + len(decisions),
        "after_dedup": len(records),
        "duplicates_removed": len(decisions),
    }
    if not isinstance(counts, Mapping) or dict(counts) != expected_counts:
        raise IntegrityError("deduplicated_corpus counts do not match its own records/decisions")
    if dedup.get("records_hash") != object_hash({"records": records}):
        raise IntegrityError("records_hash does not match deduplicated_corpus content")
    return dict(dedup)


def describe_engine_contract() -> dict[str, Any]:
    """Return the byte-stable, documented engine/WebUI artifact contract."""
    body = {"version": ENGINE_CONTRACT_VERSION, "artifacts": ARTIFACT_CONTRACTS}
    return {**body, "contract_hash": object_hash(body)}


_FIXTURE_RIS = "TY  - JOUR\nTI  - Contract fixture study\nPY  - 2022\nER  - \n"
_FIXTURE_FULL_TEXT = "Sample size was 240 participants."


def build_compatibility_fixture() -> dict[str, Any]:
    """Build one small, fixed, end-to-end compatibility fixture bundle.

    Exercises every artifact named in `ARTIFACT_CONTRACTS`, end to end, from
    a fixed in-repo RIS fixture and fixed extraction/risk-of-bias/synthesis
    inputs, for one traced record: freeze import, deduplicate, protocol-lock,
    screen, package (verified against its own cache with a full
    `verify_replay` while the cache still exists), extract one field, judge
    one risk-of-bias domain, and calculate one synthesis measure. Every
    intermediate artifact is verified with its own invariants as it is
    built, so the returned bundle only ever contains already-valid content.
    No network access; the only I/O is to a scratch temporary directory that
    is discarded before this function returns.
    """
    with tempfile.TemporaryDirectory(prefix="screen-prove-contract-") as tmp:
        tmp_path = Path(tmp)
        source_path = tmp_path / "fixture.ris"
        source_path.write_text(_FIXTURE_RIS, encoding="utf-8")
        corpus = freeze_import(
            source_path, tmp_path / "corpus.json", tmp_path / "raw", source_name="contract-fixture"
        )

        dedup = deduplicate(corpus)
        lock = ProtocolLock(
            {"eligibility": ["RCT"]}, {"steps": ["screen"]}, {"decision": "enum"}, dedup["corpus_hash"]
        )
        record_id = dedup["records"][0]["record_id"]
        adapter = StaticDecisionAdapter({record_id: "include"})
        cache_dir = tmp_path / "cache"
        screening = screen_records(dedup, lock, adapter, cache_dir, human_adjudication="off")
        package = build_package(screening)
        package_path = tmp_path / "package.json"
        write_package(package_path, package)
        verify_replay(package_path, lock, dedup, cache_dir)
        package_artifact = read_json(package_path)
        # Preserve the cached screening request/response evidence -- keyed by
        # request_hash, exactly as `screen_records` wrote it -- so an "at
        # rest" bundle can later be re-verified with a full `verify_replay`
        # (see `_verify_screening_replay`), catching a `screening_package`
        # edited alone (without a matching edit to this preserved cache).
        # This is not an independent, out-of-band authenticity anchor: see
        # the module docstring for the paired-tamper boundary this bundle
        # format does not enforce.
        replay_cache = {path.stem: read_json(path) for path in sorted(cache_dir.glob("*.json"))}

        source_hash = sha256_bytes(_FIXTURE_FULL_TEXT.encode("utf-8"))
        field = extract_field(
            _FIXTURE_FULL_TEXT, source_hash, "contract-fixture-fulltext.txt", "sample_size", 16, 19
        )
        extraction = build_extraction_record(
            record_id, source_hash, "contract-fixture-fulltext.txt", [field]
        )
        verify_extraction_record(extraction)

        domain = build_domain_judgment(
            "randomization_process", "low_risk", "computer-generated sequence reported", ["Methods p.2"]
        )
        rob = build_rob_assessment(record_id, "protocol-rob-v1", [domain], "low_risk")
        verify_rob_assessment(rob)

        recipe = build_analysis_recipe(record_id, "risk_difference")
        counts = {"treated_events": 30, "treated_total": 150, "control_events": 15, "control_total": 150}
        synthesis = calculate_effect_measure(recipe, counts)
        verify_calculation(synthesis)

    artifacts = {
        "protocol_lock": lock.to_dict(),
        "frozen_corpus": corpus,
        "deduplicated_corpus": dedup,
        "screening_package": package_artifact,
        "extraction_record": extraction,
        "risk_of_bias_assessment": rob,
        "synthesis_calculation": synthesis,
    }
    body = {"version": ENGINE_CONTRACT_VERSION, "artifacts": artifacts, "replay_cache": replay_cache}
    return {**body, "bundle_hash": object_hash(body)}


def _verify_screening_replay(artifacts: Mapping[str, Any], replay_cache: Any) -> None:
    """Re-verify the stored `screening_package` artifact with a full
    `replay.verify_replay`, using this bundle's own preserved
    `replay_cache` evidence -- not merely a `package_hash` recomputation.

    A recomputed `package_hash` alone only proves a package is internally
    self-consistent: a tampered `decision`, `reason`, or envelope
    `response_hash` can be paired with a freshly recomputed `package_hash`
    and would still pass that check. Replaying the package against the
    request/response cache captured when the fixture was built (and
    preserved in the bundle as `replay_cache`) additionally proves each
    result's `envelope.request_hash` was really derived from this bundle's
    record/lock/adapter, and that each result's decision/reason/
    `response_hash` matches the response recorded in `replay_cache` for
    that request, exactly as `verify_replay` would check against a live
    on-disk cache -- catching an edit made to *either* `screening_package`
    or `replay_cache` alone.

    This is not, however, independent evidence the way a genuinely
    separate, out-of-band `cache_dir` is: `replay_cache` is preserved
    inside this same self-hashed bundle as `screening_package`, and a
    caller with the ability to rewrite the whole bundle can edit both the
    package and the corresponding `replay_cache` entry to match (updating
    `envelope.response_hash`, the cache entry's `response_hash`, and then
    `package_hash`/`bundle_hash`) and this check will not catch that
    coordinated edit -- see the module docstring and
    `tests/test_contract.py::test_verify_compatibility_fixture_does_not_detect_a_paired_package_and_cache_tamper`.
    """
    if not isinstance(replay_cache, Mapping) or not replay_cache:
        raise IntegrityError("compatibility fixture is missing replay_cache evidence")
    lock = ProtocolLock.from_dict(artifacts["protocol_lock"])
    dedup = artifacts["deduplicated_corpus"]
    with tempfile.TemporaryDirectory(prefix="screen-prove-contract-verify-") as tmp:
        tmp_path = Path(tmp)
        package_path = tmp_path / "package.json"
        write_json(package_path, dict(artifacts["screening_package"]))
        cache_dir = tmp_path / "cache"
        cache_dir_resolved = cache_dir.resolve()
        for request_hash, entry in replay_cache.items():
            if not isinstance(request_hash, str) or not _is_sha256(request_hash):
                raise IntegrityError(
                    "compatibility fixture has an invalid replay_cache key "
                    f"(must be a SHA-256 hex digest): {request_hash!r}"
                )
            if not isinstance(entry, Mapping):
                raise IntegrityError("compatibility fixture has an invalid replay_cache entry")
            entry_path = cache_dir / f"{request_hash}.json"
            entry_path_resolved = entry_path.resolve()
            if entry_path_resolved.parent != cache_dir_resolved:
                raise IntegrityError(
                    f"compatibility fixture replay_cache key resolves outside cache_dir: {request_hash!r}"
                )
            write_json(entry_path, dict(entry))
        verify_replay(package_path, lock, dedup, cache_dir)


def _verify_cross_artifact_linkage(artifacts: Mapping[str, Any]) -> None:
    """Confirm every later artifact in the bundle traces back to the same
    protocol lock, corpus, and record as the bundle's earlier artifacts --
    not just that each artifact independently passes its own `verified_by`
    check in isolation.

    Without this, a `screening_package`, `extraction_record`,
    `risk_of_bias_assessment`, or `synthesis_calculation` that is internally
    valid but was built for an unrelated record, protocol lock, or corpus
    could be substituted into an otherwise-valid bundle, rehashed, and
    wrongly accepted as a compatible end-to-end fixture.
    """
    lock = artifacts["protocol_lock"]
    dedup = artifacts["deduplicated_corpus"]
    package = artifacts["screening_package"]

    manifest = package.get("manifest") or {}
    if manifest.get("lock_hash") != lock.get("lock_hash"):
        raise IntegrityError("screening_package does not reference this bundle's protocol_lock")
    if manifest.get("corpus_hash") != dedup.get("corpus_hash"):
        raise IntegrityError("screening_package does not reference this bundle's deduplicated_corpus")

    corpus_record_ids = {record.get("record_id") for record in dedup.get("records", [])}
    package_record_ids = {row.get("record_id") for row in package.get("results", [])}
    if not package_record_ids or not package_record_ids <= corpus_record_ids:
        raise IntegrityError(
            "screening_package results reference records outside this bundle's deduplicated_corpus"
        )

    traced_ids = {
        artifacts["extraction_record"].get("record_id"),
        artifacts["risk_of_bias_assessment"].get("record_id"),
        artifacts["synthesis_calculation"].get("record_id"),
    }
    if len(traced_ids) != 1:
        raise IntegrityError(
            "extraction_record, risk_of_bias_assessment, and synthesis_calculation do not all "
            "trace the same record"
        )
    traced_id = next(iter(traced_ids))
    if traced_id not in corpus_record_ids:
        raise IntegrityError("traced record is not present in this bundle's deduplicated_corpus")
    if traced_id not in package_record_ids:
        raise IntegrityError("traced record is not present in this bundle's screening_package")


def verify_compatibility_fixture(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Verify an "at rest" compatibility fixture bundle from its own content.

    Raises `IntegrityError` if `bundle_hash` does not match the bundle's
    content (corruption detection -- see the module docstring for why a
    self-hash comparison like this one cannot, on its own, prove
    authenticity against a caller able to rewrite the whole bundle), if the
    bundle's `version` is unsupported, if `artifacts` does not carry
    exactly the keys `ARTIFACT_CONTRACTS` documents, if `frozen_corpus` or
    `deduplicated_corpus` fails its own artifact-only `verify_frozen_corpus`/
    `verify_deduplicated_corpus` re-verification, if `deduplicated_corpus`
    no longer matches a fresh `deduplicate(frozen_corpus)` recomputation, if
    the bundle's preserved `replay_cache` evidence is missing or does not
    let `screening_package` replay cleanly through `replay.verify_replay`
    (catching a `decision`, `reason`, or envelope hash tampered in *one* of
    `screening_package`/`replay_cache` without a matching edit to the
    other -- not a coordinated edit to both, which this bundle format
    cannot catch; see `_verify_screening_replay`), if any other individual
    artifact fails its own `verified_by` re-verification, if
    `screening_package`'s manifest does not reference this same bundle's
    `protocol_lock` and `deduplicated_corpus` (by `lock_hash`/`corpus_hash`),
    if any `screening_package` result references a record outside
    `deduplicated_corpus`, or if `extraction_record`,
    `risk_of_bias_assessment`, and `synthesis_calculation` do not all trace
    the same record that is present in both `deduplicated_corpus` and
    `screening_package` -- i.e. the whole bundle must be one coherent
    end-to-end chain, not a set of artifacts that are only individually
    valid.
    """
    body = {key: value for key, value in bundle.items() if key != "bundle_hash"}
    if bundle.get("bundle_hash") != object_hash(body):
        raise IntegrityError("bundle_hash does not match compatibility fixture content")
    if body.get("version") != ENGINE_CONTRACT_VERSION:
        raise IntegrityError(f"unsupported engine contract version: {body.get('version')}")
    artifacts = body.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(ARTIFACT_CONTRACTS):
        raise IntegrityError("compatibility fixture does not cover every contracted artifact")

    ProtocolLock.from_dict(artifacts["protocol_lock"])
    verify_frozen_corpus(artifacts["frozen_corpus"])
    verify_deduplicated_corpus(artifacts["deduplicated_corpus"])

    recomputed_dedup = deduplicate(artifacts["frozen_corpus"])
    if recomputed_dedup != artifacts["deduplicated_corpus"]:
        raise IntegrityError(
            "deduplicated_corpus does not match a fresh recomputation from frozen_corpus"
        )

    _verify_screening_replay(artifacts, body.get("replay_cache"))
    verify_extraction_record(artifacts["extraction_record"])
    verify_rob_assessment(artifacts["risk_of_bias_assessment"])
    verify_calculation(artifacts["synthesis_calculation"])

    _verify_cross_artifact_linkage(artifacts)
    return body


def build_contract_receipt() -> dict[str, Any]:
    """Build a byte-stable receipt documenting the engine/WebUI contract and
    proving, via one concrete compatibility fixture bundle, that every
    contracted artifact still builds, verifies, and is tamper-detectable.
    """
    contract = describe_engine_contract()
    try:
        fixture = build_compatibility_fixture()
        verify_compatibility_fixture(fixture)
    except IntegrityError as exc:
        body = {
            "version": CONTRACT_RECEIPT_VERSION,
            "contract": contract,
            "fixture": None,
            "detail": f"compatibility fixture failed: {exc}",
            "passed": False,
        }
        return {**body, "receipt_hash": object_hash(body)}
    body = {
        "version": CONTRACT_RECEIPT_VERSION,
        "contract": contract,
        "fixture": fixture,
        "detail": "compatibility fixture built and verified for every contracted artifact",
        "passed": True,
    }
    return {**body, "receipt_hash": object_hash(body)}
