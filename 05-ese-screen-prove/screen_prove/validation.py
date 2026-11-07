"""Engine validation matrix: exercise every completed engine component across
normal, missing, ambiguous, malformed, boundary, and tamper cases.

This module does not add new engine behavior. It is a fixed, versioned matrix
of small fixture cases that call the *existing* `build_*`/`verify_*` entry
points of each already-completed component -- protocol locking, import/
deduplication, screening, published-label evaluation (`seal_labels`/
`verify_sealed_labels`/`compare_package`), extraction, risk-of-bias,
synthesis, replay, and the run-state (human-adjudication) gate -- and checks
that each one produces the expected outcome for a deliberately chosen
normal, missing, ambiguous, malformed, boundary, or tamper input, without
relying on any model consensus or live call.

Every case function returns a `ValidationCaseResult` that records what was
expected (`expected_outcome`, one of `"accept"`/`"reject"`) and what actually
happened (`actual_outcome`), so a case can only report `passed: True` when
the two agree. A case detail never embeds a raw exception message or a
filesystem path -- only the exception's type name -- so `build_validation_matrix`
and `build_validation_receipt` are byte-stable across repeated calls, even
though several cases build fixtures under a fresh temporary directory.

No case reads a live credential, opens a network socket, or calls a live
provider/connector; every fixture is fixed and defined in this module.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import IntegrityError, ProtocolLock, object_hash, read_json, sha256_bytes, write_json
from .dedup import deduplicate
from .evaluation import compare_package, seal_labels, verify_sealed_labels
from .extraction import build_extraction_record, estimated_field, extract_field, not_reported_field, verify_extraction_record
from .imports import freeze_import
from .replay import verify_replay, write_package
from .risk_of_bias import build_domain_judgment, build_rob_assessment, not_assessed_domain_judgment, verify_rob_assessment
from .screening import StaticDecisionAdapter, build_package, screen_records
from .synthesis import build_analysis_recipe, calculate_effect_measure, verify_calculation

VALIDATION_MATRIX_VERSION = "validation-matrix-v1"

COMPONENTS = (
    "protocol",
    "import_dedup",
    "screening",
    "evaluation",
    "extraction",
    "risk_of_bias",
    "synthesis",
    "replay",
    "run_state_gates",
)

CATEGORIES = ("normal", "missing", "ambiguous", "malformed", "boundary", "tamper")

# Per-component applicability matrix: which (component, category) combinations
# the matrix is required to cover. The default is every category for every
# component (the full 8x6 grid); a combination is exempt from that default
# only when it is listed in `EXCLUDED_COVERAGE` below with a stated reason
# grounded in that component's actual behavior (never "no case yet"). Both
# `build_validation_matrix`'s registered cases and `build_validation_receipt`
# are checked against `REQUIRED_COVERAGE`: a receipt cannot report
# `passed: True` while any required combination has no registered case, even
# if every registered case itself passes.
EXCLUDED_COVERAGE: dict[str, dict[str, str]] = {
    "protocol": {
        "ambiguous": (
            "protocol locking only hashes and format-validates already-supplied "
            "protocol/procedure/schema content; it has no matching or "
            "interpretation step that could produce an ambiguous input."
        ),
    },
    "import_dedup": {},
    "evaluation": {
        "ambiguous": (
            "sealing, verifying, and comparing published labels is an exact-match "
            "lookup of already-decided record_ids against an already-sealed label "
            "set; it has no fuzzy-matching or interpretation step that could "
            "produce an ambiguous input."
        ),
    },
    "screening": {
        "ambiguous": (
            "screening records exactly the decision supplied by the adapter or "
            "cache, unmodified; it has no fuzzy-matching or interpretation step "
            "that could produce an ambiguous input (title/year matching "
            "ambiguity is exercised at the import_dedup component)."
        ),
    },
    "extraction": {},
    "risk_of_bias": {},
    "synthesis": {},
    "replay": {
        "ambiguous": (
            "replay is a pure deterministic recomputation and comparison "
            "against already-verified protocol-lock, dedup, and cache inputs; "
            "it has no interpretation step that could produce an ambiguous "
            "input."
        ),
    },
    "run_state_gates": {
        "ambiguous": (
            "the governance gate is a deterministic function of already-"
            "decided decision values and the adjudication mode; it has no "
            "interpretation step that could produce an ambiguous input."
        ),
        "tamper": (
            "build_package performs no independent hash verification of its "
            "own; the integrity of the screening artifact it consumes is "
            "verified upstream by screen_records' cache checks (see the "
            "screening component's tamper case) and downstream by "
            "verify_replay (see the replay component's tamper case). There is "
            "no run-state-gate-specific artifact to tamper."
        ),
    },
}

REQUIRED_COVERAGE: dict[str, frozenset[str]] = {
    component: frozenset(CATEGORIES) - frozenset(EXCLUDED_COVERAGE.get(component, {}))
    for component in COMPONENTS
}


@dataclass(frozen=True)
class ValidationCaseResult:
    """One validation matrix case's outcome.

    `expected_outcome` and `actual_outcome` are each `"accept"` or `"reject"`;
    `passed` is True only when they agree. `detail` is always a fixed,
    deterministic description -- it never embeds a raw exception message or a
    filesystem path, so repeated runs of the same case are byte-identical.
    """

    case_id: str
    component: str
    category: str
    expected_outcome: str
    actual_outcome: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "component": self.component,
            "category": self.category,
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "passed": self.passed,
            "detail": self.detail,
        }


def _accept(case_id: str, component: str, category: str, detail: str) -> ValidationCaseResult:
    return ValidationCaseResult(case_id, component, category, "accept", "accept", True, detail)


def _accept_failed(case_id: str, component: str, category: str, detail: str) -> ValidationCaseResult:
    return ValidationCaseResult(case_id, component, category, "accept", "accept", False, detail)


def _unexpected_rejection(case_id: str, component: str, category: str, exc: Exception) -> ValidationCaseResult:
    return ValidationCaseResult(
        case_id, component, category, "accept", "reject", False, f"unexpected rejection ({type(exc).__name__})"
    )


def _expect_reject(
    case_id: str,
    component: str,
    category: str,
    fn: Callable[[], Any],
    exceptions: tuple[type[Exception], ...] = (IntegrityError,),
) -> ValidationCaseResult:
    try:
        fn()
    except exceptions as exc:
        return ValidationCaseResult(
            case_id, component, category, "reject", "reject", True, f"rejected as expected ({type(exc).__name__})"
        )
    except Exception as exc:  # noqa: BLE001 - report, never let a case crash the matrix
        return ValidationCaseResult(
            case_id, component, category, "reject", "error", False, f"unexpected exception type ({type(exc).__name__})"
        )
    return ValidationCaseResult(
        case_id, component, category, "reject", "accept", False, "expected rejection but the operation succeeded"
    )


def _lock(input_hash: str = "b" * 64) -> ProtocolLock:
    return ProtocolLock({"eligibility": ["RCT"]}, {"steps": ["screen"]}, {"decision": "enum"}, input_hash)


# --- protocol ----------------------------------------------------------------


def case_protocol_normal_lock_build() -> ValidationCaseResult:
    case_id = "protocol_normal_lock_build"
    try:
        lock = _lock()
        restored = ProtocolLock.from_dict(lock.to_dict())
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "protocol", "normal", exc)
    if restored.to_dict() != lock.to_dict():
        return _accept_failed(case_id, "protocol", "normal", "round-tripped lock does not match the original")
    return _accept(case_id, "protocol", "normal", "protocol lock builds and round-trips through to_dict/from_dict")


def case_protocol_malformed_input_hash() -> ValidationCaseResult:
    case_id = "protocol_malformed_input_hash"

    def run() -> None:
        _lock(input_hash="not-a-sha256-digest")

    return _expect_reject(case_id, "protocol", "malformed", run)


def case_protocol_missing_required_field() -> ValidationCaseResult:
    case_id = "protocol_missing_required_field"
    raw = dict(_lock().to_dict())
    del raw["input_hash"]

    def run() -> None:
        ProtocolLock.from_dict(raw)

    return _expect_reject(case_id, "protocol", "missing", run, exceptions=(KeyError,))


def case_protocol_boundary_empty_structures() -> ValidationCaseResult:
    case_id = "protocol_boundary_empty_structures"
    try:
        lock = ProtocolLock({}, {}, {}, "b" * 64)
        restored = ProtocolLock.from_dict(lock.to_dict())
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "protocol", "boundary", exc)
    if restored.to_dict() != lock.to_dict():
        return _accept_failed(case_id, "protocol", "boundary", "round-tripped empty lock does not match the original")
    return _accept(
        case_id,
        "protocol",
        "boundary",
        "an empty-but-well-formed protocol/procedure/schema still locks and round-trips",
    )


def case_protocol_tamper_lock_hash() -> ValidationCaseResult:
    case_id = "protocol_tamper_lock_hash"
    tampered = dict(_lock().to_dict())
    tampered["protocol_hash"] = "0" * 64

    def run() -> None:
        ProtocolLock.from_dict(tampered)

    return _expect_reject(case_id, "protocol", "tamper", run)


# --- import / deduplication ---------------------------------------------------
#
# Every case below builds its fixture corpus by running the completed
# `freeze_import` RIS-parsing/provenance path end-to-end (writing a fixture
# RIS file under a fresh temporary directory, freezing it, then handing the
# resulting frozen corpus to `deduplicate`) rather than hand-assembling a
# corpus dictionary that only `deduplicate` ever sees. This is what lets the
# receipt's `import_dedup` coverage claim stand for the *import* behavior,
# not just the dedup step downstream of it.


def _freeze(text: str, filename: str = "fixture.ris") -> dict[str, Any]:
    """Freeze `text` through the real `freeze_import` path and return the
    resulting frozen corpus. The temporary source/raw directories are
    cleaned up before this returns; the returned corpus only ever embeds
    filenames (e.g. `source_file`, `provenance_file`), never a filesystem
    path, so cases built from it stay byte-stable across repeated runs.
    """
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        source_path = tmp_path / filename
        source_path.write_text(text, encoding="utf-8")
        return freeze_import(source_path, tmp_path / "corpus.json", tmp_path / "raw", source_name="fixture")


def _rehash_corpus(corpus: dict[str, Any]) -> dict[str, Any]:
    body = {key: corpus[key] for key in ("version", "source_name", "source_file", "source_hash", "records")}
    return {**body, "corpus_hash": object_hash(body)}


_NORMAL_RIS = (
    "TY  - JOUR\n"
    "TI  - Alpha study\n"
    "PY  - 2020\n"
    "ER  - \n"
    "TY  - JOUR\n"
    "TI  - Beta study\n"
    "PY  - 2021\n"
    "ER  - \n"
)

_AMBIGUOUS_RIS = (
    "TY  - JOUR\n"
    "TI  - Alpha Study\n"
    "PY  - 2020\n"
    "ER  - \n"
    "TY  - JOUR\n"
    "TI  - alpha   study\n"
    "PY  - 2020\n"
    "ER  - \n"
)

_MISSING_IDENTIFIERS_RIS = "TY  - JOUR\nTI  - Solo study\nER  - \n"


def case_dedup_normal_two_distinct_records() -> ValidationCaseResult:
    case_id = "dedup_normal_two_distinct_records"
    try:
        corpus = _freeze(_NORMAL_RIS)
        result = deduplicate(corpus)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "import_dedup", "normal", exc)
    if result["counts"]["duplicates_removed"] != 0 or result["counts"]["after_dedup"] != 2:
        return _accept_failed(case_id, "import_dedup", "normal", f"unexpected dedup counts: {result['counts']}")
    return _accept(
        case_id,
        "import_dedup",
        "normal",
        "a real RIS import of two distinct records freezes and dedups with both kept, no duplicates invented",
    )


def case_dedup_ambiguous_title_year_collision() -> ValidationCaseResult:
    case_id = "dedup_ambiguous_title_year_collision"
    try:
        corpus = _freeze(_AMBIGUOUS_RIS)
        result = deduplicate(corpus)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "import_dedup", "ambiguous", exc)
    if result["counts"]["duplicates_removed"] != 1:
        return _accept_failed(
            case_id, "import_dedup", "ambiguous", f"expected one deterministic duplicate, got {result['counts']}"
        )
    reason = result["decisions"][0]["reason"] if result["decisions"] else ""
    if reason != "duplicate_title_year":
        return _accept_failed(case_id, "import_dedup", "ambiguous", f"unexpected duplicate reason: {reason}")
    return _accept(
        case_id,
        "import_dedup",
        "ambiguous",
        "a real RIS import with a case/whitespace-ambiguous title+year match is resolved "
        "deterministically, not merged silently",
    )


def case_dedup_missing_identifying_fields() -> ValidationCaseResult:
    case_id = "dedup_missing_identifying_fields"
    try:
        corpus = _freeze(_MISSING_IDENTIFIERS_RIS)
        result = deduplicate(corpus)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "import_dedup", "missing", exc)
    if result["counts"]["after_dedup"] != 1 or result["counts"]["duplicates_removed"] != 0:
        return _accept_failed(case_id, "import_dedup", "missing", f"unexpected dedup counts: {result['counts']}")
    return _accept(
        case_id,
        "import_dedup",
        "missing",
        "a real RIS import record with no pmid, doi, or reported year is kept, never silently dropped or merged",
    )


def case_dedup_malformed_non_dict_record() -> ValidationCaseResult:
    case_id = "dedup_malformed_non_dict_record"
    corpus = _freeze(_NORMAL_RIS)
    tampered = dict(corpus)
    tampered["records"] = ["not-a-record", corpus["records"][1]]
    tampered = _rehash_corpus(tampered)

    def run() -> None:
        deduplicate(tampered)

    return _expect_reject(case_id, "import_dedup", "malformed", run)


def case_dedup_malformed_dict_missing_record_id() -> ValidationCaseResult:
    case_id = "dedup_malformed_dict_missing_record_id"
    corpus = _freeze(_NORMAL_RIS)
    # A structurally valid *dict* record (unlike the non-dict record above)
    # that is missing its `record_id` key -- deliberately and documentedly
    # rejected by `deduplicate`'s own minimum-shape guard, the same shape
    # every downstream stage already assumes (see the `dedup` module
    # docstring), rather than silently kept and only failing later as an
    # unrelated `KeyError` deep in screening or replay.
    malformed_record = {key: value for key, value in corpus["records"][0].items() if key != "record_id"}
    tampered = dict(corpus)
    tampered["records"] = [malformed_record, corpus["records"][1]]
    tampered = _rehash_corpus(tampered)

    def run() -> None:
        deduplicate(tampered)

    return _expect_reject(case_id, "import_dedup", "malformed", run)


def case_dedup_boundary_empty_record_list() -> ValidationCaseResult:
    case_id = "dedup_boundary_empty_record_list"
    try:
        corpus = _freeze("")
        result = deduplicate(corpus)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "import_dedup", "boundary", exc)
    if result["counts"] != {"identified": 0, "after_dedup": 0, "duplicates_removed": 0}:
        return _accept_failed(case_id, "import_dedup", "boundary", f"unexpected dedup counts: {result['counts']}")
    return _accept(
        case_id,
        "import_dedup",
        "boundary",
        "a real import of an empty source file freezes to zero PRISMA counts, not an error",
    )


def case_dedup_tamper_corpus_hash() -> ValidationCaseResult:
    case_id = "dedup_tamper_corpus_hash"
    corpus = _freeze(_NORMAL_RIS)
    tampered = dict(corpus)
    tampered["corpus_hash"] = "0" * 64

    def run() -> None:
        deduplicate(tampered)

    return _expect_reject(case_id, "import_dedup", "tamper", run)


# --- screening -----------------------------------------------------------------


def _screening_dedup(record_ids: list[str]) -> dict[str, Any]:
    records = [{"record_id": rid, "title": f"Fixture {rid}"} for rid in record_ids]
    return {
        "version": "dedup-v1",
        "source_hash": "a" * 64,
        "corpus_hash": "b" * 64,
        "records": records,
        "records_hash": object_hash({"records": records}),
        "decisions": [],
        "counts": {"identified": len(records), "after_dedup": len(records), "duplicates_removed": 0},
    }


def case_screening_normal_include_decision() -> ValidationCaseResult:
    case_id = "screening_normal_include_decision"
    lock = _lock()
    dedup = _screening_dedup(["validation:screen:1"])
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        adapter = StaticDecisionAdapter({"validation:screen:1": "include"})
        try:
            screening = screen_records(dedup, lock, adapter, Path(tmp), human_adjudication="off")
        except IntegrityError as exc:
            return _unexpected_rejection(case_id, "screening", "normal", exc)
        decision = screening["results"][0]["decision"]
    if decision != "include":
        return _accept_failed(case_id, "screening", "normal", f"unexpected decision: {decision}")
    return _accept(case_id, "screening", "normal", "a supplied decision is recorded exactly, unmodified")


def case_screening_missing_decision_abstains() -> ValidationCaseResult:
    case_id = "screening_missing_decision_abstains"
    lock = _lock()
    dedup = _screening_dedup(["validation:screen:1"])
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        adapter = StaticDecisionAdapter({})
        try:
            screening = screen_records(dedup, lock, adapter, Path(tmp), human_adjudication="off")
        except IntegrityError as exc:
            return _unexpected_rejection(case_id, "screening", "missing", exc)
        row = screening["results"][0]
    if row["decision"] != "abstain" or row["reason"] != "no decision supplied":
        return _accept_failed(case_id, "screening", "missing", f"unexpected handling of a missing decision: {row}")
    return _accept(
        case_id, "screening", "missing", "a record with no supplied decision becomes an explicit abstention"
    )


def case_screening_malformed_invalid_adjudication_mode() -> ValidationCaseResult:
    case_id = "screening_malformed_invalid_adjudication_mode"
    lock = _lock()
    dedup = _screening_dedup(["validation:screen:1"])
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        adapter = StaticDecisionAdapter({"validation:screen:1": "include"})

        def run() -> None:
            screen_records(dedup, lock, adapter, Path(tmp), human_adjudication="sometimes")

        return _expect_reject(case_id, "screening", "malformed", run, exceptions=(ValueError,))


def case_screening_boundary_empty_record_set() -> ValidationCaseResult:
    case_id = "screening_boundary_empty_record_set"
    lock = _lock()
    dedup = _screening_dedup([])
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        adapter = StaticDecisionAdapter({})
        try:
            screening = screen_records(dedup, lock, adapter, Path(tmp), human_adjudication="off")
        except IntegrityError as exc:
            return _unexpected_rejection(case_id, "screening", "boundary", exc)
    if screening["results"] != []:
        return _accept_failed(case_id, "screening", "boundary", f"expected zero results, got: {screening['results']}")
    return _accept(case_id, "screening", "boundary", "an empty record set screens to zero results, not an error")


def case_screening_tamper_cache_response() -> ValidationCaseResult:
    case_id = "screening_tamper_cache_response"
    lock = _lock()
    dedup = _screening_dedup(["validation:screen:1"])
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        cache_dir = Path(tmp)
        adapter = StaticDecisionAdapter({"validation:screen:1": "include"})
        screen_records(dedup, lock, adapter, cache_dir, human_adjudication="off")
        cache_files = list(cache_dir.glob("*.json"))
        if len(cache_files) != 1:
            return ValidationCaseResult(
                case_id, "screening", "tamper", "reject", "error", False,
                f"expected exactly one cache file, found {len(cache_files)}",
            )
        cached = read_json(cache_files[0])
        cached["response"]["decision"] = "exclude"
        write_json(cache_files[0], cached)

        def run() -> None:
            screen_records(dedup, lock, adapter, cache_dir, human_adjudication="off")

        return _expect_reject(case_id, "screening", "tamper", run)


# --- evaluation (seal_labels / verify_sealed_labels / compare_package) -----


def _eval_package(decisions: dict[str, str], record_ids: list[str]) -> dict[str, Any]:
    dedup = _screening_dedup(record_ids)
    lock = _lock(input_hash=dedup["corpus_hash"])
    adapter = StaticDecisionAdapter(decisions)
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        screening = screen_records(dedup, lock, adapter, Path(tmp), human_adjudication="off")
    return build_package(screening)


def case_evaluation_normal_matched_labels() -> ValidationCaseResult:
    case_id = "evaluation_normal_matched_labels"
    try:
        sealed = verify_sealed_labels(seal_labels({"eval:1": "include", "eval:2": "exclude"}))
        package = _eval_package({"eval:1": "include", "eval:2": "exclude"}, ["eval:1", "eval:2"])
        report = compare_package(package, sealed)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "evaluation", "normal", exc)
    expected = {
        "matched": 2,
        "disagreements": 0,
        "ai_abstentions": 0,
        "missing_published_label": 0,
        "missing_ai_result": 0,
        "published_label_count": 2,
    }
    if report != expected:
        return _accept_failed(case_id, "evaluation", "normal", f"unexpected comparison metrics: {report}")
    return _accept(
        case_id,
        "evaluation",
        "normal",
        "sealing, verifying, and comparing two fully-matched published labels round-trips to exact golden metrics",
    )


def case_evaluation_missing_ai_result_is_counted() -> ValidationCaseResult:
    case_id = "evaluation_missing_ai_result_is_counted"
    try:
        sealed = verify_sealed_labels(seal_labels({"eval:1": "include", "eval:2": "exclude"}))
        # Only eval:1 was ever screened; eval:2 has a published label but no
        # matching AI result.
        package = _eval_package({"eval:1": "include"}, ["eval:1"])
        report = compare_package(package, sealed)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "evaluation", "missing", exc)
    expected = {
        "matched": 1,
        "disagreements": 0,
        "ai_abstentions": 0,
        "missing_published_label": 0,
        "missing_ai_result": 1,
        "published_label_count": 2,
    }
    if report != expected:
        return _accept_failed(case_id, "evaluation", "missing", f"unexpected comparison metrics: {report}")
    return _accept(
        case_id,
        "evaluation",
        "missing",
        "a published-labeled record never screened by the adapter is counted as missing_ai_result, "
        "not silently dropped",
    )


def case_evaluation_malformed_invalid_label_value() -> ValidationCaseResult:
    case_id = "evaluation_malformed_invalid_label_value"

    def run() -> None:
        seal_labels({"eval:1": "maybe"})

    return _expect_reject(case_id, "evaluation", "malformed", run, exceptions=(ValueError,))


def case_evaluation_boundary_empty_labels_and_package() -> ValidationCaseResult:
    case_id = "evaluation_boundary_empty_labels_and_package"
    try:
        sealed = verify_sealed_labels(seal_labels({}))
        package = _eval_package({}, [])
        report = compare_package(package, sealed)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "evaluation", "boundary", exc)
    expected = {
        "matched": 0,
        "disagreements": 0,
        "ai_abstentions": 0,
        "missing_published_label": 0,
        "missing_ai_result": 0,
        "published_label_count": 0,
    }
    if report != expected:
        return _accept_failed(case_id, "evaluation", "boundary", f"unexpected comparison metrics: {report}")
    return _accept(
        case_id,
        "evaluation",
        "boundary",
        "an empty sealed-label set and an empty package compare to all-zero metrics, not an error",
    )


def case_evaluation_tamper_labels_hash() -> ValidationCaseResult:
    case_id = "evaluation_tamper_labels_hash"
    sealed = seal_labels({"eval:1": "include"})
    tampered = dict(sealed)
    tampered["labels"] = {"eval:1": "exclude"}

    def run() -> None:
        verify_sealed_labels(tampered)

    return _expect_reject(case_id, "evaluation", "tamper", run)


# --- extraction ------------------------------------------------------------


_EXTRACTION_TEXT = "Sample size was 240 participants."
_EXTRACTION_SOURCE_HASH = sha256_bytes(_EXTRACTION_TEXT.encode("utf-8"))


def case_extraction_normal_reported_field() -> ValidationCaseResult:
    case_id = "extraction_normal_reported_field"
    try:
        field = extract_field(_EXTRACTION_TEXT, _EXTRACTION_SOURCE_HASH, "fixture.txt", "sample_size", 16, 19)
        record = build_extraction_record("validation:extraction:1", _EXTRACTION_SOURCE_HASH, "fixture.txt", [field])
        verify_extraction_record(record)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "extraction", "normal", exc)
    return _accept(case_id, "extraction", "normal", "a reported field builds and verifies from its own source span")


def case_extraction_missing_not_reported_field() -> ValidationCaseResult:
    case_id = "extraction_missing_not_reported_field"
    try:
        field = not_reported_field("sample_size")
        record = build_extraction_record("validation:extraction:2", _EXTRACTION_SOURCE_HASH, "fixture.txt", [field])
        verify_extraction_record(record)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "extraction", "missing", exc)
    if record["fields"][0]["status"] != "not_reported":
        return _accept_failed(case_id, "extraction", "missing", f"unexpected status: {record['fields'][0]}")
    return _accept(
        case_id,
        "extraction",
        "missing",
        "a field the source text never states is recorded as explicit not_reported (NR), never invented",
    )


def case_extraction_ambiguous_estimated_without_note_is_rejected() -> ValidationCaseResult:
    case_id = "extraction_ambiguous_estimated_without_note_is_rejected"

    def run() -> None:
        estimated_field("attrition_rate", "12%", "")

    return _expect_reject(case_id, "extraction", "ambiguous", run)


def case_extraction_boundary_zero_length_span_is_rejected() -> ValidationCaseResult:
    case_id = "extraction_boundary_zero_length_span_is_rejected"

    def run() -> None:
        extract_field(_EXTRACTION_TEXT, _EXTRACTION_SOURCE_HASH, "fixture.txt", "sample_size", 5, 5)

    return _expect_reject(case_id, "extraction", "boundary", run)


def case_extraction_malformed_span_out_of_range() -> ValidationCaseResult:
    case_id = "extraction_malformed_span_out_of_range"

    def run() -> None:
        extract_field(_EXTRACTION_TEXT, _EXTRACTION_SOURCE_HASH, "fixture.txt", "sample_size", 16, 999)

    return _expect_reject(case_id, "extraction", "malformed", run)


def case_extraction_tamper_value_mismatch() -> ValidationCaseResult:
    case_id = "extraction_tamper_value_mismatch"
    field = extract_field(_EXTRACTION_TEXT, _EXTRACTION_SOURCE_HASH, "fixture.txt", "sample_size", 16, 19)
    record = build_extraction_record("validation:extraction:1", _EXTRACTION_SOURCE_HASH, "fixture.txt", [field])
    tampered = dict(record)
    tampered["fields"] = [dict(record["fields"][0])]
    tampered["fields"][0]["value"] = "9999"

    def run() -> None:
        verify_extraction_record(tampered)

    return _expect_reject(case_id, "extraction", "tamper", run)


# --- risk of bias --------------------------------------------------------------


def case_rob_normal_domain_judgment() -> ValidationCaseResult:
    case_id = "rob_normal_domain_judgment"
    try:
        domain = build_domain_judgment("randomization_process", "low_risk", "computer-generated sequence", ["Methods p.2"])
        assessment = build_rob_assessment("validation:rob:1", "protocol-rob-v1", [domain], "low_risk")
        verify_rob_assessment(assessment)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "risk_of_bias", "normal", exc)
    return _accept(case_id, "risk_of_bias", "normal", "a domain judgment builds and verifies with its rationale")


def case_rob_ambiguous_not_assessed_with_source_links() -> ValidationCaseResult:
    case_id = "rob_ambiguous_not_assessed_with_source_links"

    def run() -> None:
        build_domain_judgment("randomization_process", "not_assessed", "no data reported", ["Methods p.2"])

    return _expect_reject(case_id, "risk_of_bias", "ambiguous", run)


def case_rob_missing_domain_recorded_as_not_assessed() -> ValidationCaseResult:
    case_id = "rob_missing_domain_recorded_as_not_assessed"
    try:
        domain = not_assessed_domain_judgment(
            "blinding_of_outcome_assessors", "no outcome-assessor blinding data reported"
        )
        assessment = build_rob_assessment("validation:rob:2", "protocol-rob-v1", [domain], "not_assessed")
        verify_rob_assessment(assessment)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "risk_of_bias", "missing", exc)
    if assessment["domains"][0]["judgment"] != "not_assessed":
        return _accept_failed(case_id, "risk_of_bias", "missing", f"unexpected judgment: {assessment['domains'][0]}")
    return _accept(
        case_id,
        "risk_of_bias",
        "missing",
        "a domain with no supporting evidence is recorded as an explicit not_assessed abstention, not invented",
    )


def case_rob_malformed_source_links_not_strings() -> ValidationCaseResult:
    case_id = "rob_malformed_source_links_not_strings"

    def run() -> None:
        build_domain_judgment("randomization_process", "low_risk", "computer-generated sequence", [123])

    return _expect_reject(case_id, "risk_of_bias", "malformed", run)


def case_rob_boundary_invented_overall_when_all_not_assessed_is_rejected() -> ValidationCaseResult:
    case_id = "rob_boundary_invented_overall_when_all_not_assessed_is_rejected"
    domain = not_assessed_domain_judgment("randomization_process", "no data reported")

    def run() -> None:
        build_rob_assessment("validation:rob:4", "protocol-rob-v1", [domain], "low_risk")

    return _expect_reject(case_id, "risk_of_bias", "boundary", run)


def case_rob_tamper_domain_judgment() -> ValidationCaseResult:
    case_id = "rob_tamper_domain_judgment"
    domain = build_domain_judgment("randomization_process", "low_risk", "computer-generated sequence", ["Methods p.2"])
    assessment = build_rob_assessment("validation:rob:1", "protocol-rob-v1", [domain], "low_risk")
    tampered = dict(assessment)
    tampered["domains"] = [dict(assessment["domains"][0])]
    tampered["domains"][0]["judgment"] = "high_risk"

    def run() -> None:
        verify_rob_assessment(tampered)

    return _expect_reject(case_id, "risk_of_bias", "tamper", run)


# --- synthesis -------------------------------------------------------------------


def case_synthesis_normal_risk_difference() -> ValidationCaseResult:
    case_id = "synthesis_normal_risk_difference"
    try:
        recipe = build_analysis_recipe("validation:synthesis:1", "risk_difference")
        counts = {"treated_events": 30, "treated_total": 150, "control_events": 15, "control_total": 150}
        calculation = calculate_effect_measure(recipe, counts)
        verify_calculation(calculation)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "synthesis", "normal", exc)
    if calculation["status"] != "calculated":
        return _accept_failed(case_id, "synthesis", "normal", f"unexpected status: {calculation['status']}")
    # Golden closed-form values (hand-verified): p1=0.2, p0=0.1 -> RD=0.1
    # exactly; SE = sqrt(0.2*0.8/150 + 0.1*0.9/150) = sqrt(1/600); margin =
    # 1.959963984540054 * SE, giving ci_low/ci_high below.
    golden = (
        math.isclose(calculation["point_estimate"], 0.1, abs_tol=1e-9)
        and math.isclose(calculation["ci_low"], 0.0199848, abs_tol=1e-4)
        and math.isclose(calculation["ci_high"], 0.1800152, abs_tol=1e-4)
    )
    if not golden:
        return _accept_failed(
            case_id,
            "synthesis",
            "normal",
            f"risk-difference calculation does not match its golden closed-form values: {calculation}",
        )
    return _accept(
        case_id,
        "synthesis",
        "normal",
        "a risk-difference calculation succeeds, verifies, and matches its golden closed-form point estimate and CI",
    )


def case_synthesis_normal_odds_ratio() -> ValidationCaseResult:
    case_id = "synthesis_normal_odds_ratio"
    try:
        recipe = build_analysis_recipe("validation:synthesis:6", "odds_ratio")
        counts = {"treated_events": 20, "treated_total": 100, "control_events": 10, "control_total": 100}
        calculation = calculate_effect_measure(recipe, counts)
        verify_calculation(calculation)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "synthesis", "normal", exc)
    if calculation["status"] != "calculated":
        return _accept_failed(case_id, "synthesis", "normal", f"unexpected status: {calculation['status']}")
    # Golden closed-form values (hand-verified): a=20/100 treated, b=10/100
    # control -> OR = (20*90)/(10*80) = 2.25 exactly; SE = sqrt(1/20 + 1/80 +
    # 1/10 + 1/90) = 5/12 exactly, giving ci_low/ci_high below.
    golden = (
        math.isclose(calculation["point_estimate"], 2.25, abs_tol=1e-9)
        and math.isclose(calculation["ci_low"], 0.994295, abs_tol=1e-3)
        and math.isclose(calculation["ci_high"], 5.091548, abs_tol=1e-3)
    )
    if not golden:
        return _accept_failed(
            case_id,
            "synthesis",
            "normal",
            f"odds-ratio calculation does not match its golden closed-form values: {calculation}",
        )
    return _accept(
        case_id,
        "synthesis",
        "normal",
        "an odds-ratio calculation succeeds, verifies, and matches its golden closed-form point estimate and CI",
    )


def case_synthesis_boundary_zero_event_cell() -> ValidationCaseResult:
    case_id = "synthesis_boundary_zero_event_cell"
    try:
        recipe = build_analysis_recipe("validation:synthesis:2", "risk_ratio")
        counts = {"treated_events": 0, "treated_total": 100, "control_events": 5, "control_total": 100}
        calculation = calculate_effect_measure(recipe, counts)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "synthesis", "boundary", exc)
    if calculation["status"] != "insufficient_data" or not calculation["uncertainty_flags"]:
        return _accept_failed(case_id, "synthesis", "boundary", "a zero-event cell was not flagged as insufficient")
    return _accept(
        case_id, "synthesis", "boundary", "a zero-event ratio-measure cell is recorded as insufficient_data, never guessed"
    )


def case_synthesis_missing_count_field() -> ValidationCaseResult:
    case_id = "synthesis_missing_count_field"
    recipe = build_analysis_recipe("validation:synthesis:3", "risk_difference")
    counts = {"treated_events": 30, "treated_total": 150, "control_events": 15}

    def run() -> None:
        calculate_effect_measure(recipe, counts)

    return _expect_reject(case_id, "synthesis", "missing", run)


def case_synthesis_ambiguous_unsupported_measure_is_rejected() -> ValidationCaseResult:
    case_id = "synthesis_ambiguous_unsupported_measure_is_rejected"

    def run() -> None:
        build_analysis_recipe("validation:synthesis:4", "hazard_ratio")

    return _expect_reject(case_id, "synthesis", "ambiguous", run)


def case_synthesis_malformed_events_exceed_total() -> ValidationCaseResult:
    case_id = "synthesis_malformed_events_exceed_total"
    recipe = build_analysis_recipe("validation:synthesis:5", "risk_difference")
    counts = {"treated_events": 200, "treated_total": 150, "control_events": 15, "control_total": 150}

    def run() -> None:
        calculate_effect_measure(recipe, counts)

    return _expect_reject(case_id, "synthesis", "malformed", run)


def case_synthesis_tamper_point_estimate() -> ValidationCaseResult:
    case_id = "synthesis_tamper_point_estimate"
    recipe = build_analysis_recipe("validation:synthesis:1", "risk_difference")
    counts = {"treated_events": 30, "treated_total": 150, "control_events": 15, "control_total": 150}
    calculation = calculate_effect_measure(recipe, counts)
    tampered = dict(calculation)
    tampered["point_estimate"] = 0.99

    def run() -> None:
        verify_calculation(tampered)

    return _expect_reject(case_id, "synthesis", "tamper", run)


# --- replay ----------------------------------------------------------------------


def _replay_fixture() -> tuple[ProtocolLock, dict[str, Any]]:
    records = [{"record_id": "validation:replay:1", "title": "Fixture study"}]
    dedup = {
        "version": "dedup-v1",
        "source_hash": "a" * 64,
        "corpus_hash": "b" * 64,
        "records": records,
        "records_hash": object_hash({"records": records}),
        "decisions": [],
        "counts": {"identified": 1, "after_dedup": 1, "duplicates_removed": 0},
    }
    lock = _lock(input_hash=dedup["corpus_hash"])
    return lock, dedup


def case_replay_normal_round_trip() -> ValidationCaseResult:
    case_id = "replay_normal_round_trip"
    lock, dedup = _replay_fixture()
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        adapter = StaticDecisionAdapter({"validation:replay:1": "include"})
        try:
            screening = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")
            package = build_package(screening)
            package_path = tmp_path / "package.json"
            write_package(package_path, package)
            verify_replay(package_path, lock, dedup, tmp_path / "cache")
        except IntegrityError as exc:
            return _unexpected_rejection(case_id, "replay", "normal", exc)
    return _accept(case_id, "replay", "normal", "screen/package/replay round-trips without modification")


def case_replay_malformed_missing_manifest() -> ValidationCaseResult:
    case_id = "replay_malformed_missing_manifest"
    lock, dedup = _replay_fixture()
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        adapter = StaticDecisionAdapter({"validation:replay:1": "include"})
        screening = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")
        package = build_package(screening)
        package["manifest"] = {}
        package_path = tmp_path / "package.json"
        write_package(package_path, package)

        def run() -> None:
            verify_replay(package_path, lock, dedup, tmp_path / "cache")

        return _expect_reject(case_id, "replay", "malformed", run)


def case_replay_missing_record_coverage_is_rejected() -> ValidationCaseResult:
    case_id = "replay_missing_record_coverage_is_rejected"
    lock, dedup = _replay_fixture()
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        adapter = StaticDecisionAdapter({"validation:replay:1": "include"})
        screening = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")
        package = build_package(screening)
        package["results"] = []
        package_path = tmp_path / "package.json"
        write_package(package_path, package)

        def run() -> None:
            verify_replay(package_path, lock, dedup, tmp_path / "cache")

        return _expect_reject(case_id, "replay", "missing", run)


def case_replay_missing_cache_evidence_is_rejected() -> ValidationCaseResult:
    case_id = "replay_missing_cache_evidence_is_rejected"
    lock, dedup = _replay_fixture()
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        cache_dir = tmp_path / "cache"
        adapter = StaticDecisionAdapter({"validation:replay:1": "include"})
        screening = screen_records(dedup, lock, adapter, cache_dir, human_adjudication="off")
        package = build_package(screening)
        package_path = tmp_path / "package.json"
        write_package(package_path, package)
        # Delete the cached request/response evidence a real replay would
        # need -- the package itself is untouched, only its supporting
        # cache evidence is missing.
        for cache_file in cache_dir.glob("*.json"):
            cache_file.unlink()

        def run() -> None:
            verify_replay(package_path, lock, dedup, cache_dir)

        return _expect_reject(case_id, "replay", "missing", run)


def case_replay_boundary_empty_result_set() -> ValidationCaseResult:
    case_id = "replay_boundary_empty_result_set"
    records: list[dict[str, Any]] = []
    dedup = {
        "version": "dedup-v1",
        "source_hash": "a" * 64,
        "corpus_hash": "b" * 64,
        "records": records,
        "records_hash": object_hash({"records": records}),
        "decisions": [],
        "counts": {"identified": 0, "after_dedup": 0, "duplicates_removed": 0},
    }
    lock = _lock(input_hash=dedup["corpus_hash"])
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        adapter = StaticDecisionAdapter({})
        try:
            screening = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")
            package = build_package(screening)
            package_path = tmp_path / "package.json"
            write_package(package_path, package)
            verify_replay(package_path, lock, dedup, tmp_path / "cache")
        except IntegrityError as exc:
            return _unexpected_rejection(case_id, "replay", "boundary", exc)
    return _accept(
        case_id, "replay", "boundary", "an empty screening package replays cleanly with zero records, not an error"
    )


def case_replay_tamper_decision_changed() -> ValidationCaseResult:
    case_id = "replay_tamper_decision_changed"
    lock, dedup = _replay_fixture()
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        tmp_path = Path(tmp)
        adapter = StaticDecisionAdapter({"validation:replay:1": "include"})
        screening = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")
        package = build_package(screening)
        package_path = tmp_path / "package.json"
        write_package(package_path, package)
        tampered = read_json(package_path)
        tampered["results"][0]["decision"] = "exclude"
        write_json(package_path, tampered)

        def run() -> None:
            verify_replay(package_path, lock, dedup, tmp_path / "cache")

        return _expect_reject(case_id, "replay", "tamper", run)


# --- run-state (human-adjudication) gates ----------------------------------------


def _run_state_screening(decisions: dict[str, str], mode: str, record_ids: list[str]) -> dict[str, Any]:
    dedup = _screening_dedup(record_ids)
    lock = _lock(input_hash=dedup["corpus_hash"])
    adapter = StaticDecisionAdapter(decisions)
    with tempfile.TemporaryDirectory(prefix="screen-prove-validate-") as tmp:
        return screen_records(dedup, lock, adapter, Path(tmp), human_adjudication=mode)


def case_run_state_normal_clear_queue_is_governed() -> ValidationCaseResult:
    case_id = "run_state_normal_clear_queue_is_governed"
    try:
        screening = _run_state_screening({"g1": "include", "g2": "exclude"}, "on", ["g1", "g2"])
        package = build_package(screening)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "run_state_gates", "normal", exc)
    if package["adjudication_queue"]["blocking"] or not package["governed_export_allowed"]:
        return _accept_failed(case_id, "run_state_gates", "normal", f"unexpected governance state: {package}")
    return _accept(
        case_id, "run_state_gates", "normal", "with no pending abstentions, governed export is allowed"
    )


def case_run_state_normal_off_mode_ai_only_package_ignores_abstention() -> ValidationCaseResult:
    case_id = "run_state_normal_off_mode_ai_only_package_ignores_abstention"
    try:
        # o2 has no supplied decision, so it screens to an explicit abstain.
        screening = _run_state_screening({"o1": "include"}, "off", ["o1", "o2"])
        package = build_package(screening)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "run_state_gates", "normal", exc)
    queue = package["adjudication_queue"]
    if (
        package["package_type"] != "AI_ONLY_SCREENING_PACKAGE"
        or package["governed_export_allowed"] is not False
        or queue["blocking"] is not False
        or queue["unresolved_record_ids"] != []
    ):
        return _accept_failed(
            case_id, "run_state_gates", "normal", f"unexpected off-mode governance state: {package}"
        )
    return _accept(
        case_id,
        "run_state_gates",
        "normal",
        "with human_adjudication off, an abstention still yields an AI_ONLY_SCREENING_PACKAGE with "
        "governed export always disallowed and no invented adjudication-queue entry",
    )


def case_run_state_missing_adjudication_blocks_export() -> ValidationCaseResult:
    case_id = "run_state_missing_adjudication_blocks_export"
    try:
        screening = _run_state_screening({"m1": "include"}, "on", ["m1", "m2"])
        package = build_package(screening)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "run_state_gates", "missing", exc)
    queue = package["adjudication_queue"]
    if not queue["blocking"] or package["governed_export_allowed"] or queue["unresolved_record_ids"] != ["m2"]:
        return _accept_failed(case_id, "run_state_gates", "missing", f"a missing decision did not block export: {package}")
    return _accept(
        case_id,
        "run_state_gates",
        "missing",
        "a record with no supplied decision blocks governed export until adjudicated",
    )


def case_run_state_boundary_empty_queue_is_governed() -> ValidationCaseResult:
    case_id = "run_state_boundary_empty_queue_is_governed"
    try:
        screening = _run_state_screening({}, "on", [])
        package = build_package(screening)
    except IntegrityError as exc:
        return _unexpected_rejection(case_id, "run_state_gates", "boundary", exc)
    if package["adjudication_queue"]["blocking"] or not package["governed_export_allowed"]:
        return _accept_failed(case_id, "run_state_gates", "boundary", f"unexpected governance state: {package}")
    return _accept(
        case_id,
        "run_state_gates",
        "boundary",
        "an empty screening result set is vacuously governed, with no abstentions to block export",
    )


def case_run_state_malformed_invalid_mode() -> ValidationCaseResult:
    case_id = "run_state_malformed_invalid_mode"
    screening = dict(_run_state_screening({"x1": "include"}, "on", ["x1"]))
    screening["human_adjudication"] = "sometimes"

    def run() -> None:
        build_package(screening)

    return _expect_reject(case_id, "run_state_gates", "malformed", run)


ALL_VALIDATION_CASES: tuple[Callable[[], ValidationCaseResult], ...] = (
    case_protocol_normal_lock_build,
    case_protocol_missing_required_field,
    case_protocol_malformed_input_hash,
    case_protocol_boundary_empty_structures,
    case_protocol_tamper_lock_hash,
    case_dedup_normal_two_distinct_records,
    case_dedup_missing_identifying_fields,
    case_dedup_ambiguous_title_year_collision,
    case_dedup_malformed_non_dict_record,
    case_dedup_malformed_dict_missing_record_id,
    case_dedup_boundary_empty_record_list,
    case_dedup_tamper_corpus_hash,
    case_screening_normal_include_decision,
    case_screening_missing_decision_abstains,
    case_screening_malformed_invalid_adjudication_mode,
    case_screening_boundary_empty_record_set,
    case_screening_tamper_cache_response,
    case_evaluation_normal_matched_labels,
    case_evaluation_missing_ai_result_is_counted,
    case_evaluation_malformed_invalid_label_value,
    case_evaluation_boundary_empty_labels_and_package,
    case_evaluation_tamper_labels_hash,
    case_extraction_normal_reported_field,
    case_extraction_missing_not_reported_field,
    case_extraction_ambiguous_estimated_without_note_is_rejected,
    case_extraction_malformed_span_out_of_range,
    case_extraction_boundary_zero_length_span_is_rejected,
    case_extraction_tamper_value_mismatch,
    case_rob_normal_domain_judgment,
    case_rob_missing_domain_recorded_as_not_assessed,
    case_rob_ambiguous_not_assessed_with_source_links,
    case_rob_malformed_source_links_not_strings,
    case_rob_boundary_invented_overall_when_all_not_assessed_is_rejected,
    case_rob_tamper_domain_judgment,
    case_synthesis_normal_risk_difference,
    case_synthesis_normal_odds_ratio,
    case_synthesis_missing_count_field,
    case_synthesis_ambiguous_unsupported_measure_is_rejected,
    case_synthesis_malformed_events_exceed_total,
    case_synthesis_boundary_zero_event_cell,
    case_synthesis_tamper_point_estimate,
    case_replay_normal_round_trip,
    case_replay_missing_record_coverage_is_rejected,
    case_replay_missing_cache_evidence_is_rejected,
    case_replay_malformed_missing_manifest,
    case_replay_boundary_empty_result_set,
    case_replay_tamper_decision_changed,
    case_run_state_normal_clear_queue_is_governed,
    case_run_state_normal_off_mode_ai_only_package_ignores_abstention,
    case_run_state_missing_adjudication_blocks_export,
    case_run_state_malformed_invalid_mode,
    case_run_state_boundary_empty_queue_is_governed,
)


def build_validation_matrix() -> list[ValidationCaseResult]:
    """Run every registered validation case and return its result, in order."""
    return [case() for case in ALL_VALIDATION_CASES]


def missing_required_coverage(results: list[ValidationCaseResult]) -> list[dict[str, str]]:
    """Return every `(component, category)` pair `REQUIRED_COVERAGE` demands
    that has no registered, executed case among `results`, as a sorted list
    of `{"component": ..., "category": ...}` entries (empty when coverage is
    complete). Coverage is computed from the *executed* results, not just
    the static registry, so a case that silently fails to register still
    surfaces as missing coverage here.
    """
    present = {(result.component, result.category) for result in results}
    missing = [
        {"component": component, "category": category}
        for component in COMPONENTS
        for category in CATEGORIES
        if category in REQUIRED_COVERAGE[component] and (component, category) not in present
    ]
    return missing


def build_validation_receipt() -> dict[str, Any]:
    """Build a byte-stable validation receipt covering every registered case.

    `passed` is True only if every case's `actual_outcome` matched its
    `expected_outcome` *and* every `(component, category)` combination
    required by `REQUIRED_COVERAGE` has at least one registered, executed
    case -- an incomplete registry can never report `passed: True` merely
    because the cases it does contain all happened to pass. Any required
    combination with no case is listed in `missing_coverage`, and any
    combination deliberately exempted from the default full 8x6 grid (with
    its documented reason) is listed in `excluded_coverage`. No case reads a
    live credential, opens a network socket, or calls a live provider/
    connector; every fixture is fixed and defined in this module.
    """
    results = build_validation_matrix()
    missing_coverage = missing_required_coverage(results)
    body = {
        "version": VALIDATION_MATRIX_VERSION,
        "components": list(COMPONENTS),
        "categories": list(CATEGORIES),
        "required_coverage": {
            component: sorted(REQUIRED_COVERAGE[component]) for component in COMPONENTS
        },
        "excluded_coverage": {
            component: dict(sorted(EXCLUDED_COVERAGE.get(component, {}).items()))
            for component in COMPONENTS
        },
        "missing_coverage": missing_coverage,
        "cases": [result.to_dict() for result in results],
        "passed": bool(not missing_coverage and all(result.passed for result in results)),
    }
    return {**body, "matrix_hash": object_hash(body)}
