"""Local WebUI server for operating the offline ESE increment."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from bridge import BridgeError, EngineBridge, read_json
from provider_call_plan import build_call_plan
from provider_gate import issue_gate
from provider_health import check_provider, plan_registry_checks
from provider_manifest import build_health_request
from providers import ProviderConfigError, provider_registry, public_view, validate_provider_config
from full_text_access import ACCESS_PLAN_VERSION, build_access_plan, connector_registry
from full_text_config import (
    ACCESS_CONFIG_VERSION,
    access_config_registry,
    validate_access_config,
)
from full_text_probe import build_probe_plan
from manual_access_record import record_manual_access
from store import RunStore, StageOrderError, StoreError


STATIC_ROOT = Path(__file__).with_name("static")

# This package implements docs/UI_BUILD_LOOP.md stages 1 through 9. On top
# of stage 1's operator shell (create/select a run, offline-only status,
# artifact listing) and its "doctor" diagnostic, stage 2's "import" (upload a
# RIS/NBIB source export, preserve it unmodified, produce the engine's
# frozen import artifact), stage 3's "dedup"/"lock" (deterministically
# deduplicate a run's frozen records and lock the run's protocol against
# that deduplicated set), and stage 4's "screen"/"adjudicate"/
# "human_adjudication" (cached screening, an abstention blocking queue, and
# an explicit on/off human-adjudication toggle), stage 5 adds exactly two
# more run actions -- "package" (bundle a fully-screened run's stage
# artifacts into a single portable package artifact) and "replay"
# (deterministically reproduce that package's artifacts and compare them,
# byte for byte, against the originals). Package/artifact-inspection
# (reading the full content of any individual artifact, not merely its
# name/size/hash) and a replay comparison report are exposed as read-only
# GET routes alongside these two actions -- see `do_GET`. This package also
# implements stage 7's "extract" (source-anchored extraction with
# NR/estimated handling), stage 8's "rob" (bounded risk-of-bias assessment),
# and stage 9's "synthesize" (bounded single-study risk ratio/risk
# difference/odds ratio calculation with explicit insufficient-data flags);
# none of these three remain future work. Stage 6 ("Compatibility", a
# pinned engine-artifact contract check) and phase 10 ("Full offline
# stack") are verification gates over this same scope, not additional run
# actions -- see docs/UI_BUILD_LOOP.md and docs/reviews/ for their current,
# independently confirmed status.
VALID_ACTIONS = ("doctor", "import", "dedup", "lock", "screen", "adjudicate", "human_adjudication", "package", "replay", "extract", "rob", "synthesize")

# The actual stage gate: "dedup" cannot run before this run's "import" stage
# is recorded complete (there would be no frozen artifact to deduplicate),
# "lock" cannot run before "dedup" is complete (there would be nothing
# deterministic to lock the protocol against), "screen" cannot run before
# "lock" is complete (there would be no locked protocol to screen against),
# "package" cannot run before "screen" is complete (bundling a run whose
# screening is not yet complete -- either never run or still blocked on
# adjudication -- would package an incomplete/unresolved result as if it
# were final), and "replay" cannot run before "package" is complete (there
# would be no package artifact to reproduce). "doctor" and "import" have no
# prerequisite: "doctor" is a stage-independent diagnostic and "import" only
# requires the run to exist, which is already guaranteed by the time a run
# id is reachable at all (run creation itself marks "initialized" complete).
# "adjudicate" and "human_adjudication" are deliberately absent from this
# map: "adjudicate" has its own, more specific gate (the run's "screen"
# stage must currently read "blocked", not merely "complete" -- see the
# "adjudicate" branch of `WebUIHandler._run_locked_action`), and
# "human_adjudication" is a run setting a caller may flip at any time,
# including before a run has ever been screened.
ACTION_PREREQUISITES: dict[str, str] = {
    "dedup": "import",
    "lock": "dedup",
    "screen": "lock",
    "package": "screen",
    "replay": "package",
    "extract": "screen",
    "rob": "extraction",
    "synthesize": "rob",
}

# The fixed set of stage artifacts every package artifact (`package.json`)
# must bundle a sha256 manifest entry for, and the exact set of files a
# `replay` invocation is required to reproduce under this run's `replay/`
# directory. Deliberately excludes `adjudications.json`: it is this WebUI's
# own record of human decisions, not an engine-produced artifact, and is not
# part of what "replay" reproduces.
PACKAGE_ARTIFACT_NAMES = ("frozen.json", "dedup.json", "lock.json", "screen.json")

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# The only decision values the offline engine's cached screening output
# (screen.json) may report per record. "abstain" is the value that opens a
# blocking-queue entry (docs/UI_BUILD_LOOP.md stage 4, "Verify abstention
# and blocking queue"); "include"/"exclude" are final decisions.
SCREEN_DECISION_VALUES = {"include", "exclude", "abstain"}

# The only decision values a human adjudicator may resolve a blocking-queue
# entry with. Deliberately excludes "abstain": adjudication exists to
# resolve an abstention into a final call, not to record another
# abstention.
ADJUDICATION_DECISION_VALUES = {"include", "exclude"}

# RIS and NBIB are the only source export formats this stage understands.
# The mapping value is a display/response label only -- the engine's real
# `import` command has no `--format` flag and determines the source format
# itself from the input file.
SUPPORTED_IMPORT_SUFFIXES = {".ris": "ris", ".nbib": "nbib"}

# A conservative allowlist for uploaded file names: no path separators, no
# leading dot/hidden files, no drive letters -- just a plain name so the raw
# upload can only ever be written inside this run's own `source/` directory,
# never escape it via `..`, an absolute path, or a backslash-style traversal
# segment.
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_EXTRACTION_STATUSES = {"reported", "not_reported", "estimated"}
_ROB_JUDGMENTS = {"low_risk", "some_concerns", "high_risk", "unclear_risk", "not_assessed"}
_SYNTHESIS_MEASURES = {"risk_ratio", "risk_difference", "odds_ratio"}
_SYNTHESIS_COUNTS = ("treated_events", "treated_total", "control_events", "control_total")
_Z_95 = 1.959963984540054


def _object_hash(value: object) -> str:
    """Match the engine's canonical JSON object hash for local validation."""
    body = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _validate_extraction_artifact(
    path: Path,
    *,
    source_bytes: bytes,
    record_id: str,
    provenance_file: str,
    requested_fields: list[dict[str, object]],
) -> dict[str, object]:
    """Verify the engine-produced extraction artifact before recording success.

    The engine is the authority for construction; this WebUI-side contract
    check prevents a zero exit code or malformed replacement artifact from
    being presented as a completed stage.
    """
    payload = read_json(path)
    if payload.get("version") != "extraction-record-v1":
        raise BridgeError("extract reported success with an unsupported extraction artifact version")
    if payload.get("record_id") != record_id:
        raise BridgeError("extraction artifact record_id does not match the requested record")
    if payload.get("provenance_file") != provenance_file:
        raise BridgeError("extraction artifact provenance_file does not match its source artifact")
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if payload.get("source_hash") != source_hash:
        raise BridgeError("extraction artifact source_hash does not match the uploaded source bytes")
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise BridgeError("extraction artifact must contain a non-empty fields array")
    expected_fields: list[tuple[str, str]] = []
    for entry in requested_fields:
        field = entry.get("field")
        status = entry.get("status", "reported")
        if not isinstance(field, str) or not field.strip():
            raise BridgeError("extraction field specifications require non-empty field names")
        if status not in _EXTRACTION_STATUSES:
            raise BridgeError(f"unsupported requested extraction field status: {status!r}")
        expected_fields.append((field, status))
    if len(fields) != len(expected_fields):
        raise BridgeError("extraction artifact field count does not match the requested specification")
    seen: set[str] = set()
    full_text = source_bytes.decode("utf-8")
    for entry, (expected_field, expected_status) in zip(fields, expected_fields):
        if not isinstance(entry, dict):
            raise BridgeError("each extraction field must be a JSON object")
        field = entry.get("field")
        status = entry.get("status")
        if (
            not isinstance(field, str)
            or not field.strip()
            or field in seen
            or field != expected_field
            or status != expected_status
        ):
            raise BridgeError("extraction fields must have unique non-empty names")
        if status not in _EXTRACTION_STATUSES:
            raise BridgeError(f"unsupported extraction field status: {status!r}")
        seen.add(field)
        if status == "reported":
            span = entry.get("span")
            if not isinstance(span, dict) or span.get("source_hash") != source_hash:
                raise BridgeError(f"reported extraction field '{field}' is missing source provenance")
            if span.get("provenance_file") != provenance_file:
                raise BridgeError(f"reported extraction field '{field}' has mismatched provenance")
            start, end, quote = span.get("start"), span.get("end"), span.get("quote")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not isinstance(quote, str)
                or not (0 <= start < end <= len(full_text))
                or not quote.strip()
                or not isinstance(entry.get("value"), str)
                or full_text[start:end] != quote
                or entry.get("value", "").strip() != quote.strip()
            ):
                raise BridgeError(f"reported extraction field '{field}' has an invalid source span")
        elif status == "not_reported":
            if entry.get("value") != "" or entry.get("span") is not None:
                raise BridgeError(f"NR extraction field '{field}' contains unsupported data")
        elif (
            not isinstance(entry.get("value"), str)
            or not entry["value"].strip()
            or not isinstance(entry.get("estimation_note"), str)
            or not entry["estimation_note"].strip()
            or entry.get("span") is not None
        ):
            raise BridgeError(f"ESTIMATED extraction field '{field}' is incomplete")
    record_hash = payload.get("record_hash")
    body = {key: value for key, value in payload.items() if key != "record_hash"}
    if not isinstance(record_hash, str) or not _SHA256_HEX.fullmatch(record_hash):
        raise BridgeError("extraction artifact is missing a valid record_hash")
    # `_object_hash` serializes `body` with `allow_nan=False` (the engine's
    # own canonical hash contract): a `body` containing a non-finite float
    # (NaN/Infinity) anywhere -- including in an unvalidated top-level field
    # this function does not otherwise inspect -- raises `TypeError`/
    # `ValueError` rather than returning a mismatched string. Without this
    # guard (mirroring `_validate_synthesis_artifact`'s own
    # `calculation_hash` check below) that exception would escape this
    # function's own documented "Fails closed (raises BridgeError)"
    # contract instead of being reported as the artifact-content mismatch
    # it actually is.
    try:
        matches_hash = record_hash == _object_hash(body)
    except (TypeError, ValueError):
        matches_hash = False
    if not matches_hash:
        raise BridgeError("extraction artifact record_hash does not match its content")
    return payload


def _validate_rob_artifact(
    path: Path,
    *,
    requested_spec: dict[str, object],
    extraction_record_id: str,
) -> dict[str, object]:
    """Verify the engine-produced bounded risk-of-bias artifact."""
    payload = read_json(path)
    if payload.get("version") != "rob-assessment-v1":
        raise BridgeError("rob reported success with an unsupported assessment artifact version")
    record_id = requested_spec.get("record_id")
    if record_id != extraction_record_id or payload.get("record_id") != record_id:
        raise BridgeError("risk-of-bias record_id must match the extracted included record")
    tool = requested_spec.get("tool")
    if not isinstance(tool, str) or payload.get("tool") != tool.strip():
        raise BridgeError("risk-of-bias artifact tool does not match the requested tool")
    overall = requested_spec.get("overall_judgment")
    if payload.get("overall_judgment") != overall:
        raise BridgeError("risk-of-bias artifact overall_judgment does not match the request")
    requested_domains = requested_spec.get("domains")
    domains = payload.get("domains")
    if not isinstance(requested_domains, list) or not requested_domains:
        raise BridgeError("risk-of-bias request must contain a non-empty domains array")
    if not isinstance(domains, list) or len(domains) != len(requested_domains):
        raise BridgeError("risk-of-bias artifact domain count does not match the request")
    seen: set[str] = set()
    for emitted, requested in zip(domains, requested_domains):
        if not isinstance(emitted, dict) or not isinstance(requested, dict):
            raise BridgeError("risk-of-bias domains must be JSON objects")
        expected_domain = requested.get("domain")
        expected_judgment = requested.get("judgment")
        expected_rationale = requested.get("rationale")
        expected_links = requested.get("source_links", [])
        if not isinstance(expected_domain, str) or not expected_domain.strip():
            raise BridgeError("risk-of-bias request domains require non-empty names")
        if expected_judgment not in _ROB_JUDGMENTS:
            raise BridgeError(f"unsupported requested risk-of-bias judgment: {expected_judgment!r}")
        if not isinstance(expected_rationale, str) or not expected_rationale.strip():
            raise BridgeError(f"risk-of-bias request domain '{expected_domain}' has no rationale")
        if not isinstance(expected_links, list) or any(
            not isinstance(link, str) or not link.strip() for link in expected_links
        ):
            raise BridgeError(f"risk-of-bias request domain '{expected_domain}' has invalid source_links")
        domain = emitted.get("domain")
        judgment = emitted.get("judgment")
        if (
            not isinstance(domain, str)
            or domain != expected_domain.strip()
            or judgment != expected_judgment
            or domain in seen
        ):
            raise BridgeError("risk-of-bias artifact domains do not match the request")
        seen.add(domain)
        rationale = emitted.get("rationale")
        links = emitted.get("source_links")
        if not isinstance(rationale, str) or not rationale.strip():
            raise BridgeError(f"risk-of-bias domain '{domain}' has no rationale")
        if not isinstance(links, list) or any(not isinstance(link, str) or not link.strip() for link in links):
            raise BridgeError(f"risk-of-bias domain '{domain}' has invalid source_links")
        if rationale != expected_rationale.strip() or links != [link.strip() for link in expected_links]:
            raise BridgeError(f"risk-of-bias domain '{domain}' does not match the submitted rationale or source_links")
        if judgment == "not_assessed" and links:
            raise BridgeError(f"not_assessed risk-of-bias domain '{domain}' cannot carry source_links")
    rob_hash = payload.get("rob_hash")
    body = {key: value for key, value in payload.items() if key != "rob_hash"}
    if not isinstance(rob_hash, str) or not _SHA256_HEX.fullmatch(rob_hash):
        raise BridgeError("risk-of-bias artifact is missing a valid rob_hash")
    # Same guard as `_validate_extraction_artifact`'s `record_hash` check
    # above, for the same reason: `_object_hash` can raise on a `body`
    # containing a non-finite float in an unvalidated field, and that must
    # be reported as this function's own documented `BridgeError` contract
    # mismatch, not escape as a bare `TypeError`/`ValueError`.
    try:
        matches_hash = rob_hash == _object_hash(body)
    except (TypeError, ValueError):
        matches_hash = False
    if not matches_hash:
        raise BridgeError("risk-of-bias artifact rob_hash does not match its content")
    return payload


def _validate_synthesis_artifact(
    path: Path,
    *,
    requested_spec: dict[str, object],
    extraction_record_id: str,
) -> dict[str, object]:
    """Verify one engine-produced single-study synthesis calculation."""
    payload = read_json(path)
    if payload.get("version") != "synthesis-calculation-v1":
        raise BridgeError("synthesis reported success with an unsupported calculation artifact version")
    record_id = requested_spec.get("record_id")
    if record_id != extraction_record_id or payload.get("record_id") != record_id:
        raise BridgeError("synthesis record_id must match the extracted included record")
    measure = requested_spec.get("measure")
    if measure not in _SYNTHESIS_MEASURES or payload.get("measure") != measure:
        raise BridgeError("synthesis artifact measure does not match the requested measure")
    requested_counts = requested_spec.get("counts")
    inputs = payload.get("inputs")
    if not isinstance(requested_counts, dict) or not isinstance(inputs, dict):
        raise BridgeError("synthesis counts and artifact inputs must be JSON objects")
    if set(requested_counts) != set(_SYNTHESIS_COUNTS) or inputs != requested_counts:
        raise BridgeError("synthesis artifact inputs do not match the requested counts")
    counts: dict[str, int] = {}
    for key in _SYNTHESIS_COUNTS:
        value = requested_counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BridgeError(f"synthesis count '{key}' must be a non-negative integer")
        counts[key] = value
    if counts["treated_events"] > counts["treated_total"]:
        raise BridgeError("synthesis treated_events must not exceed treated_total")
    if counts["control_events"] > counts["control_total"]:
        raise BridgeError("synthesis control_events must not exceed control_total")
    if counts["treated_total"] == 0 or counts["control_total"] == 0:
        raise BridgeError("synthesis treatment and control totals must be greater than zero")
    a, n1 = counts["treated_events"], counts["treated_total"]
    b, n0 = counts["control_events"], counts["control_total"]
    flags: list[str] = []
    if measure in ("risk_ratio", "odds_ratio") and (a == 0 or b == 0):
        flags.append("zero_event_cell_ratio_measure_undefined")
    if measure == "odds_ratio" and (a == n1 or b == n0):
        flags.append("zero_nonevent_cell_odds_ratio_undefined")
    expected_status = "insufficient_data" if flags else "calculated"
    if payload.get("status") != expected_status or payload.get("uncertainty_flags") != flags:
        raise BridgeError("synthesis artifact status or uncertainty_flags do not match the counts")
    if flags:
        expected_values = {"point_estimate": None, "ci_low": None, "ci_high": None}
    else:
        p1, p0 = a / n1, b / n0
        try:
            if measure == "risk_ratio":
                point = p1 / p0
                se = math.sqrt(1 / a - 1 / n1 + 1 / b - 1 / n0)
                ci_low = math.exp(math.log(point) - _Z_95 * se)
                ci_high = math.exp(math.log(point) + _Z_95 * se)
            elif measure == "odds_ratio":
                point = (a * (n0 - b)) / (b * (n1 - a))
                se = math.sqrt(1 / a + 1 / (n1 - a) + 1 / b + 1 / (n0 - b))
                ci_low = math.exp(math.log(point) - _Z_95 * se)
                ci_high = math.exp(math.log(point) + _Z_95 * se)
            else:
                point = p1 - p0
                se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
                ci_low = point - _Z_95 * se
                ci_high = point + _Z_95 * se
        except (ArithmeticError, OverflowError) as exc:
            raise BridgeError(f"synthesis calculation could not be reproduced: {exc}") from exc
        expected_values = {"point_estimate": point, "ci_low": ci_low, "ci_high": ci_high}
    for key, expected in expected_values.items():
        actual = payload.get(key)
        if expected is None:
            if actual is not None:
                raise BridgeError(f"synthesis artifact field '{key}' must be null for insufficient data")
        elif not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(actual) or actual != expected:
            raise BridgeError(f"synthesis artifact field '{key}' does not match the calculation")
    calculation_hash = payload.get("calculation_hash")
    body = {key: value for key, value in payload.items() if key != "calculation_hash"}
    if not isinstance(calculation_hash, str) or not _SHA256_HEX.fullmatch(calculation_hash):
        raise BridgeError("synthesis artifact is missing a valid calculation_hash")
    try:
        matches_hash = calculation_hash == _object_hash(body)
    except (TypeError, ValueError):
        matches_hash = False
    if not matches_hash:
        raise BridgeError("synthesis artifact calculation_hash does not match its content")
    return payload


def _sha256_file(path: Path) -> str:
    """Return the hex-encoded sha256 digest of a file's exact on-disk
    bytes, read in fixed-size chunks so this scales to artifacts far
    larger than available memory. This is the "verify artifact hashes"
    half of stage 3's contract (docs/UI_BUILD_LOOP.md stage 3,
    "deduplication and lock"): a deterministic, content-addressed digest
    an operator can use to confirm a dedup/lock artifact is exactly the
    bytes the engine produced, not a truncated or corrupted write, and
    that re-running the same deterministic input reproduces the same
    output."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _locked_record_ids(lock_path: Path, dedup_path: Path | None = None) -> set[str]:
    """Return the set of record ids this run's locked protocol
    (`lock.json`) actually locked -- the "one decision per record"
    contract's actual reference population (docs/UI_BUILD_LOOP.md stage 4,
    "screening"): the fixed set of record ids `screen`'s cached output is
    required to report exactly one decision for, no more and no fewer.

    Called from `_screening_state` (not only from the "screen" action
    itself, which already checks `lock_path.is_file()` before invoking the
    engine) because `_screening_state` is also reached from the
    "screening" GET route, "adjudicate", and "human_adjudication" -- none
    of which re-verify the lock artifact is still on disk before this. A
    recorded-complete "lock" stage is not itself proof the artifact it
    depends on is still on disk (for example if the run directory were
    tampered with out-of-band), so this fails closed instead of trusting
    stage bookkeeping.

    Fails closed (raises `BridgeError`) if `lock.json` is missing, or if
    its shape does not match this WebUI's locked-record contract: a JSON
    object with a non-empty `records` array of `{"id": <non-empty
    string>}` objects, with no `id` repeated. The non-empty check exists
    for the same reason `_screening_state`'s own `decisions` non-empty
    check does: an empty `records` array is still shape-valid JSON and
    would otherwise silently make every screen artifact -- even a
    genuinely empty one -- appear to have "complete coverage" of nothing.
    """
    if not lock_path.is_file():
        raise BridgeError(
            "screening requires the run's lock artifact (lock.json) on disk to determine "
            "this run's locked record set"
        )
    payload = read_json(lock_path)
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        if dedup_path is None or not dedup_path.is_file():
            raise BridgeError(
                "lock artifact 'records' must be a non-empty JSON array identifying this run's "
                "locked record set"
            )
        dedup_payload = read_json(dedup_path)
        if payload.get("input_hash") != dedup_payload.get("corpus_hash"):
            raise BridgeError(
                "lock artifact input_hash does not match the current deduplication artifact"
            )
        records = dedup_payload.get("records")
        id_key = "record_id"
    else:
        id_key = "id"
    locked_ids: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            raise BridgeError("each lock artifact record must be a JSON object")
        record_id = item.get(id_key)
        if not isinstance(record_id, str) or not record_id:
            raise BridgeError("each lock artifact record 'id' must be a non-empty string")
        if record_id in locked_ids:
            raise BridgeError(
                f"lock artifact reports more than one record with id '{record_id}'"
            )
        locked_ids.add(record_id)
    return locked_ids


def _screening_state(path: Path) -> dict[str, object]:
    """Derive a run's current screening/adjudication state from the
    engine's cached `screen.json` output (if any) and this WebUI's own
    `adjudications.json` record of human decisions made against that
    output's abstained records (docs/UI_BUILD_LOOP.md stage 4,
    "screening"): every decision the engine produced, which of those are
    abstentions, which abstentions a human has already resolved, and which
    abstentions remain in the blocking queue.

    Returns an all-empty state if screening has never been run for this
    run (no `screen.json` on disk yet), so callers do not need to special-
    case "not screened yet" separately from "screened with zero
    abstentions".

    Fails closed (raises `BridgeError`) if `screen.json` exists but its
    shape does not match the contract this WebUI requires (a JSON object
    with a non-empty `decisions` array of `{"id": <non-empty string>,
    "decision": <one of SCREEN_DECISION_VALUES>}` objects, with no `id`
    repeated) -- the same "a zero exit code is necessary but not
    sufficient" posture already enforced for `import`/`dedup`/`lock`'s own
    output artifacts. The repeated-`id` check enforces the "one decision
    per record" contract's other half: shape validation alone would still
    let an engine output that reports two different (or even
    contradictory) decisions for the same record id mark screening
    complete. The non-empty check closes the same contract's other false-
    success gap: an engine output whose `decisions` array is empty (or
    simply missing every locked record) has not actually screened
    anything and must not be trusted as a complete run, even though an
    empty list is a shape-valid JSON array and would otherwise pass every
    other check here.

    Also fails closed if the screen artifact's decision ids do not exactly
    match this run's locked record set (`_locked_record_ids`, read from
    `lock.json`): every locked record id must have exactly one decision,
    and no decision may name a record id that is not part of this run's
    locked set. Without this, a screen artifact containing a decision for
    an arbitrary/unknown id, or simply omitting some locked records, would
    still pass the checks above (non-empty, well-shaped, no repeated id)
    and mark screening complete -- the actual "one decision per record"
    contract requires "record" to mean this run's locked records
    specifically, not merely whatever ids happen to appear in the screen
    artifact.

    Also fails closed if this run's `adjudications.json` (this WebUI's own
    record of human decisions against the blocking queue, not engine
    output) does not exactly match the current screening output's
    abstained records: every key must identify a record id this run's
    current `screen.json` actually reports as abstained, and every value
    must be one of `ADJUDICATION_DECISION_VALUES`. Without this, a
    corrupted or hand-edited `adjudications.json` entry -- for example a
    key that does not correspond to any abstained record, or a value like
    `"abstain"` that is not a valid resolution -- would be silently
    accepted as resolving a blocking-queue entry (or silently ignored),
    either of which can incorrectly discharge the "screen" stage's block.
    """
    screen_path = path / "screen.json"
    if not screen_path.is_file():
        return {"decisions": [], "abstained_ids": [], "adjudications": {}, "remaining": []}
    payload = read_json(screen_path)
    decisions = payload.get("decisions")
    if decisions is None and isinstance(payload.get("results"), list):
        decisions = [
            {"id": item.get("record_id"), "decision": item.get("decision")}
            for item in payload["results"]
            if isinstance(item, dict)
        ]
    if not isinstance(decisions, list):
        raise BridgeError("screen artifact 'decisions' must be a JSON array")
    if not decisions:
        raise BridgeError(
            "screen artifact 'decisions' must not be empty; screening requires at least "
            "one decision per locked record"
        )
    abstained_ids: list[str] = []
    seen_ids: set[str] = set()
    for item in decisions:
        if not isinstance(item, dict):
            raise BridgeError("each screen artifact decision must be a JSON object")
        record_id = item.get("id")
        decision = item.get("decision")
        if not isinstance(record_id, str) or not record_id:
            raise BridgeError("each screen artifact decision 'id' must be a non-empty string")
        if not isinstance(decision, str) or decision not in SCREEN_DECISION_VALUES:
            raise BridgeError(
                f"screen artifact decision for '{record_id}' must be one of "
                f"{sorted(SCREEN_DECISION_VALUES)}, got {decision!r}"
            )
        if record_id in seen_ids:
            raise BridgeError(
                f"screen artifact reports more than one decision for record id '{record_id}'; "
                "this WebUI requires exactly one decision per record"
            )
        seen_ids.add(record_id)
        if decision == "abstain":
            abstained_ids.append(record_id)
    locked_ids = _locked_record_ids(path / "lock.json", path / "dedup.json")
    missing_ids = sorted(locked_ids - seen_ids)
    unknown_ids = sorted(seen_ids - locked_ids)
    if missing_ids or unknown_ids:
        problems = []
        if missing_ids:
            problems.append(f"missing a decision for locked record id(s) {missing_ids}")
        if unknown_ids:
            problems.append(
                f"reports decision(s) for id(s) {unknown_ids} not in this run's locked record set"
            )
        raise BridgeError(
            "screen artifact must report exactly one decision per locked record; " + "; ".join(problems)
        )
    adjudications_path = path / "adjudications.json"
    adjudications: dict[str, str] = {}
    if adjudications_path.is_file():
        try:
            loaded = json.loads(adjudications_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError(f"cannot read adjudications.json: {exc}") from exc
        if not isinstance(loaded, dict):
            raise BridgeError("adjudications.json must contain a JSON object")
        abstained_id_set = set(abstained_ids)
        for key, value in loaded.items():
            if not isinstance(key, str) or key not in abstained_id_set:
                raise BridgeError(
                    f"adjudications.json key {key!r} does not identify an abstained record "
                    "in this run's current screening output"
                )
            if not isinstance(value, str) or value not in ADJUDICATION_DECISION_VALUES:
                raise BridgeError(
                    f"adjudications.json value for '{key}' must be one of "
                    f"{sorted(ADJUDICATION_DECISION_VALUES)}, got {value!r}"
                )
            adjudications[key] = value
    remaining = [record_id for record_id in abstained_ids if record_id not in adjudications]
    return {
        "decisions": decisions,
        "abstained_ids": abstained_ids,
        "adjudications": adjudications,
        "remaining": remaining,
    }


def _write_adjudications(path: Path, adjudications: dict[str, str]) -> None:
    (path / "adjudications.json").write_text(
        json.dumps(adjudications, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _ensure_offline_decisions(
    dedup_path: Path, lock_path: Path, decisions_path: Path
) -> None:
    """Create an explicit abstention map when no cached decisions exist.

    This stage has no live model connector, so an absent decision is
    represented honestly as an explicit abstention for every deduplicated
    record. The engine's real `screen` command requires a decisions file;
    defaulting to abstain never fabricates an include/exclude decision. A
    separately approved cached decision map may replace this file before a
    later screen run.
    """
    if decisions_path.is_file():
        return
    payload = read_json(dedup_path)
    records = payload.get("records")
    if isinstance(records, list) and records:
        locked_ids = []
        seen_ids: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise BridgeError(
                    "each dedup artifact record must be a JSON object before screening"
                )
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                raise BridgeError(
                    "each dedup artifact record 'record_id' must be a non-empty string before screening"
                )
            if record_id in seen_ids:
                raise BridgeError(f"dedup artifact repeats record id '{record_id}'")
            seen_ids.add(record_id)
            locked_ids.append(record_id)
    else:
        locked_ids = sorted(_locked_record_ids(lock_path, dedup_path))
    decisions: dict[str, str] = {}
    for record_id in locked_ids:
        decisions[record_id] = "abstain"
    decisions_path.write_text(
        json.dumps(decisions, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _screen_stage_status(human_adjudication: str, remaining: list) -> str:
    """The actual "abstention and blocking queue" gate
    (docs/UI_BUILD_LOOP.md stage 4): a run's "screen" stage reads
    "blocked" -- not "complete" -- exactly when human adjudication is
    currently required (`human_adjudication == "on"`) AND at least one
    abstained record has not yet been resolved by `adjudicate`
    (`remaining` is non-empty). Any other combination (adjudication off,
    or no unresolved abstentions) reads "complete": abstentions never
    block the run by default, matching stage 4's "human adjudication off
    by default" requirement."""
    return "blocked" if human_adjudication == "on" and remaining else "complete"


def _read_package_manifest(path: Path) -> dict[str, str]:
    """Read and validate this run's package artifact (`package.json`)
    against the actual "byte-stable offline replay" contract's other half
    (docs/UI_BUILD_LOOP.md stage 5, "replay and audit"): the fixed
    reference set of sha256 digests a `replay` invocation's reproduced
    artifacts are later compared against.

    Fails closed (raises `BridgeError`) if `package.json` is missing, or if
    its shape does not match this WebUI's contract: a JSON object with a
    `manifest` field that is itself a JSON object covering *exactly*
    `PACKAGE_ARTIFACT_NAMES` -- no missing artifact (which would let a
    replay silently skip verifying it) and no unexpected extra artifact
    (which would silently expand what "byte-stable" claims to cover beyond
    this WebUI's actual fixed set) -- with every value a well-formed
    64-character lowercase hex sha256 digest string."""
    package_path = path / "package.json"
    if not package_path.is_file():
        raise BridgeError(
            "replay requires the run's package artifact (package.json) on disk"
        )
    payload = read_json(package_path)
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        raise BridgeError("package artifact 'manifest' must be a JSON object")
    missing = [name for name in PACKAGE_ARTIFACT_NAMES if name not in manifest]
    unexpected = sorted(name for name in manifest if name not in PACKAGE_ARTIFACT_NAMES)
    if missing or unexpected:
        problems = []
        if missing:
            problems.append(f"missing manifest entry for {missing}")
        if unexpected:
            problems.append(f"has unexpected manifest entry for {unexpected}")
        raise BridgeError(
            f"package artifact 'manifest' must cover exactly {list(PACKAGE_ARTIFACT_NAMES)}; "
            + "; ".join(problems)
        )
    validated: dict[str, str] = {}
    for name in PACKAGE_ARTIFACT_NAMES:
        digest = manifest[name]
        if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
            raise BridgeError(
                f"package artifact manifest entry for '{name}' must be a 64-character "
                "lowercase hex sha256 digest string"
            )
        validated[name] = digest
    return validated


def _replay_comparison(path: Path, manifest: dict[str, str]) -> dict[str, object]:
    """Compare this run's `replay/` artifacts against `manifest` (this
    run's package artifact's recorded sha256 digests), one artifact at a
    time, for every name in `PACKAGE_ARTIFACT_NAMES`. This is the actual
    "byte-stable offline replay" verification (docs/UI_BUILD_LOOP.md stage
    5): a replayed artifact is only considered to match if its current
    on-disk sha256 is byte-identical to the digest recorded in the package
    manifest, not merely present."""
    comparison: dict[str, dict[str, object]] = {}
    mismatched: list[str] = []
    for name in PACKAGE_ARTIFACT_NAMES:
        replay_path = path / "replay" / name
        replay_hash = _sha256_file(replay_path) if replay_path.is_file() else None
        expected_hash = manifest[name]
        match = replay_hash == expected_hash
        comparison[name] = {
            "expected_sha256": expected_hash,
            "replay_sha256": replay_hash,
            "match": match,
        }
        if not match:
            mismatched.append(name)
    return {"comparison": comparison, "mismatched": mismatched, "byte_stable": not mismatched}


def _replay_state(path: Path) -> dict[str, object]:
    """Derive a run's current package/replay comparison state without
    triggering a new `replay` invocation, for the read-only GET route
    (docs/UI_BUILD_LOOP.md stage 5, "expose package, comparison, replay").

    Returns `packaged: False` (and no comparison) if this run has never
    been packaged. Returns `packaged: True, replayed: False` if it has been
    packaged but `replay` has never produced a complete set of
    `PACKAGE_ARTIFACT_NAMES` under `replay/` yet -- a partial or missing
    replay output is treated the same as "never replayed", not as a
    mismatch, since no actual byte comparison has meaningfully run yet.
    Otherwise returns the full byte-for-byte comparison from
    `_replay_comparison`."""
    package_path = path / "package.json"
    if not package_path.is_file():
        return {
            "packaged": False,
            "replayed": False,
            "byte_stable": None,
            "comparison": {},
            "mismatched": [],
        }
    manifest = _read_package_manifest(path)
    replay_dir = path / "replay"
    replayed = replay_dir.is_dir() and all(
        (replay_dir / name).is_file() for name in PACKAGE_ARTIFACT_NAMES
    )
    if not replayed:
        return {
            "packaged": True,
            "replayed": False,
            "byte_stable": None,
            "comparison": {},
            "mismatched": [],
        }
    result = _replay_comparison(path, manifest)
    return {"packaged": True, "replayed": True, **result}


def _artifact_content(path: Path, name: str) -> dict[str, object]:
    """Read a single artifact's exact on-disk bytes for operator inspection
    (docs/UI_BUILD_LOOP.md stage 5, "artifact inspection") -- unlike
    `RunStore.artifacts()` (name/size/hash only), this returns the actual
    content, transported as base64 exactly like the `import` upload path
    (never decoded/re-encoded through a text codec, so this is safe for
    non-UTF-8 preserved source uploads too).

    Fails closed (raises `StoreError`) if `name` is empty, contains a `..`
    or empty path segment (path traversal), resolves outside this run's own
    directory, names `run.json` (this WebUI's internal run manifest, never
    listed as an artifact either -- see `RunStore.artifacts()`), or does
    not identify an existing file."""
    if not name:
        raise StoreError("artifact name is required")
    segments = name.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise StoreError("invalid artifact path")
    run_root = path.resolve()
    target = (path / name).resolve()
    if target == run_root or run_root not in target.parents:
        raise StoreError("invalid artifact path")
    if target.name == "run.json" or not target.is_file():
        raise StoreError(f"artifact '{name}' not found")
    content = target.read_bytes()
    return {
        "name": name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "ESE-WebUI/0.1"

    @property
    def app(self) -> "WebUIApp":
        return self.server.app  # type: ignore[attr-defined]

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise StoreError("request body is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise StoreError("request body must be a JSON object")
        return value

    def _error(self, exc: Exception, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json({"error": str(exc)}, status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._serve_static("index.html")
        if parsed.path.startswith("/static/"):
            return self._serve_static(parsed.path.removeprefix("/static/"))
        try:
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["api", "health"]:
                return self._send_json(self.app.health())
            if parts == ["api", "providers"]:
                return self._send_json(
                    {"registry": provider_registry(), "checks": plan_registry_checks()}
                )
            if parts == ["api", "full-text", "connectors"]:
                return self._send_json(
                    {
                        "access_version": ACCESS_PLAN_VERSION,
                        "connectors": connector_registry(),
                    }
                )
            if parts == ["api", "full-text", "access-config"]:
                return self._send_json(
                    {
                        "access_config_version": ACCESS_CONFIG_VERSION,
                        "connectors": access_config_registry(),
                    }
                )
            if parts == ["api", "runs"]:
                return self._send_json({"runs": self.app.store.list()})
            if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                return self._send_json(self.app.store.get(parts[2])[1])
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "artifacts":
                # Validate the run id up front (outside the per-run lock),
                # matching `_run_action`'s ordering, then hold the same
                # per-run lock a POST action would hold while it mutates
                # this run's artifacts. Without this, a GET here can
                # interleave with a concurrent POST's artifact writes/
                # deletes (import's stale-artifact cleanup, a dedup/lock
                # invocation replacing dedup.json/lock.json, etc.) and
                # either enumerate a torn, mid-mutation directory listing
                # or hash a file that is being unlinked/rewritten out from
                # under it -- the same race the review flagged for the
                # POST response's own artifact snapshot.
                run_id = parts[2]
                self.app.store.get(run_id)
                with self.app.lock_for_run(run_id):
                    return self._send_json({"artifacts": self.app.store.artifacts(run_id)})
            if len(parts) >= 5 and parts[:2] == ["api", "runs"] and parts[3] == "artifacts":
                # Artifact inspection (docs/UI_BUILD_LOOP.md stage 5): the
                # remaining path segments after "artifacts" name a specific
                # artifact, joined back with "/" to support artifacts nested
                # under a subdirectory (for example "source/records.ris" or
                # "replay/frozen.json"). Same per-run locking rationale as
                # the plain "artifacts" listing route immediately above --
                # this must not read a file mid-write/mid-delete from a
                # concurrent POST against the same run.
                run_id = parts[2]
                self.app.store.get(run_id)
                name = "/".join(parts[4:])
                with self.app.lock_for_run(run_id):
                    path, _ = self.app.store.get(run_id)
                    return self._send_json(_artifact_content(path, name))
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "replay":
                # The read-only replay/comparison report (docs/UI_BUILD_LOOP.md
                # stage 5, "expose package, comparison, replay"): the current
                # package/replay byte-comparison state without triggering a
                # new replay run. Same per-run locking rationale as
                # "screening" directly below -- must not read package.json /
                # replay/* mid-write from a concurrent POST "package" or
                # "replay" action against the same run.
                run_id = parts[2]
                self.app.store.get(run_id)
                with self.app.lock_for_run(run_id):
                    path, _ = self.app.store.get(run_id)
                    return self._send_json(_replay_state(path))
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "screening":
                # Same locking rationale as the "artifacts" route directly
                # above: hold this run's lock while reading `screen.json`
                # and `adjudications.json` so a concurrent POST (a fresh
                # `screen` run replacing both, or an `adjudicate` call
                # updating the latter) cannot be read mid-mutation.
                run_id = parts[2]
                self.app.store.get(run_id)
                with self.app.lock_for_run(run_id):
                    path, record = self.app.store.get(run_id)
                    state = _screening_state(path)
                    human_adjudication = record.get("human_adjudication", "off")
                    status = _screen_stage_status(human_adjudication, state["remaining"])
                    return self._send_json(
                        {
                            "decisions": state["decisions"],
                            "abstained_ids": state["abstained_ids"],
                            "adjudications": state["adjudications"],
                            "remaining": state["remaining"],
                            "human_adjudication": human_adjudication,
                            "blocked": status == "blocked",
                        }
                    )
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (StoreError, BridgeError, ProviderConfigError) as exc:
            self._error(exc)

    def _serve_static(self, relative: str) -> None:
        target = (STATIC_ROOT / relative).resolve()
        if target.parent != STATIC_ROOT.resolve() or not target.is_file():
            return self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        try:
            body = self._read_json()
            if parts == ["api", "runs"]:
                return self._create_run(body)
            if parts == ["api", "providers", "validate"]:
                return self._provider_validate(body)
            if parts == ["api", "providers", "health-plan"]:
                return self._provider_health_plan(body)
            if parts == ["api", "providers", "manifest"]:
                return self._provider_manifest(body)
            if parts == ["api", "providers", "gate"]:
                return self._provider_gate(body)
            if parts == ["api", "providers", "call-plan"]:
                return self._provider_call_plan(body)
            if parts == ["api", "full-text", "plan"]:
                return self._full_text_plan(body)
            if parts == ["api", "full-text", "access-config", "validate"]:
                return self._full_text_access_config(body)
            if parts == ["api", "full-text", "probe-plan"]:
                return self._full_text_probe_plan(body)
            if parts == ["api", "full-text", "manual-record"]:
                return self._full_text_manual_record(body)
            if len(parts) >= 4 and parts[:2] == ["api", "runs"]:
                run_id = parts[2]
                action = parts[3]
                return self._run_action(run_id, action, body)
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except StageOrderError as exc:
            self._error(exc, HTTPStatus.CONFLICT)
        except (StoreError, BridgeError, ProviderConfigError, ValueError, json.JSONDecodeError) as exc:
            self._error(exc)
        except Exception as exc:  # fail closed with a useful local diagnostic
            self._error(exc, HTTPStatus.INTERNAL_SERVER_ERROR)
            traceback.print_exc()

    def _create_run(self, body: dict) -> None:
        record = self.app.store.create(str(body.get("name", "")))
        path, _ = self.app.store.get(record["id"])
        try:
            self.app.bridge.initialize_run(path)
            record = self.app.store.update(record["id"], stage="initialized")
        except Exception as exc:
            self.app.store.update(record["id"], stage="initialized", status="failed", error=str(exc))
            raise
        self._send_json(record, HTTPStatus.CREATED)

    def _provider_validate(self, body: dict) -> None:
        self._send_json(public_view(validate_provider_config(body)))

    def _provider_health_plan(self, body: dict) -> None:
        self._send_json(check_provider(body))

    def _provider_manifest(self, body: dict) -> None:
        self._send_json(build_health_request(body))

    def _provider_gate(self, body: dict) -> None:
        """Issue one explicit, per-run operator gate; never execute it."""
        if set(body) != {"config", "run_id", "action", "operator_ack"}:
            raise ProviderConfigError(
                "provider gate request must contain exactly config, run_id, "
                "action, and operator_ack"
            )
        self._send_json(
            issue_gate(
                body.get("config"),
                run_id=body.get("run_id"),
                action=body.get("action"),
                operator_ack=body.get("operator_ack"),
            )
        )

    def _provider_call_plan(self, body: dict) -> None:
        """Build a canonical gate-bound plan; never resolve or send."""
        if set(body) != {"config", "gate", "run_id", "action"}:
            raise ProviderConfigError(
                "provider call-plan request must contain exactly config, "
                "gate, run_id, and action"
            )
        config = body.get("config")
        self._send_json(
            build_call_plan(
                config,
                build_health_request(config),
                body.get("gate"),
                run_id=body.get("run_id"),
                action=body.get("action"),
            )
        )

    def _full_text_plan(self, body: dict) -> None:
        """Build an offline lawful-access route plan; never search or log in."""
        allowed = {"citation", "enabled_connectors", "access_config"}
        if set(body) - allowed or "citation" not in body:
            raise ProviderConfigError(
                "full-text plan request must contain citation and may contain "
                "enabled_connectors"
            )
        enabled = body.get("enabled_connectors")
        self._send_json(
            build_access_plan(
                body["citation"],
                enabled_connectors=enabled,
                access_config=body.get("access_config"),
            )
        )

    def _full_text_access_config(self, body: dict) -> None:
        """Validate references and route metadata; never resolve or connect."""
        self._send_json(validate_access_config(body))

    def _full_text_probe_plan(self, body: dict) -> None:
        """Build a Scopus/Embase metadata plan; never resolve or connect."""
        if set(body) != {"citation", "access_config"}:
            raise ProviderConfigError(
                "full-text probe-plan request must contain citation and access_config"
            )
        self._send_json(build_probe_plan(body["citation"], body["access_config"]))

    def _full_text_manual_record(self, body: dict) -> None:
        """Record user-owned access evidence without fetching or storing a file."""
        allowed = {
            "citation",
            "route_id",
            "access_state",
            "candidate",
            "source_url",
            "operator_note",
        }
        if set(body) - allowed or not {"citation", "route_id", "access_state", "candidate"}.issubset(body):
            raise ProviderConfigError(
                "manual-record request is missing required fields or contains unsupported fields"
            )
        self._send_json(
            record_manual_access(
                body["citation"],
                route_id=body["route_id"],
                access_state=body["access_state"],
                candidate=body["candidate"],
                source_url=body.get("source_url"),
                operator_note=body.get("operator_note"),
            )
        )

    def _run_action(self, run_id: str, action: str, body: dict) -> None:
        if action not in VALID_ACTIONS:
            self._send_json({"error": "unsupported action"}, HTTPStatus.NOT_FOUND)
            return
        # Validate the run id up front (outside the per-run lock) so an
        # unknown/invalid run id surfaces its ordinary StoreError instead
        # of allocating a lock-table entry for it.
        self.app.store.get(run_id)
        # `ThreadingHTTPServer` dispatches every request on its own thread,
        # so two actions against the *same* run id can otherwise execute
        # concurrently on different threads. For `import` in particular,
        # that previously let one request's stale-artifact cleanup (unlink
        # `frozen.json` / rmtree `raw/`, run further down) interleave with
        # a concurrent request's engine execution and stage update -- for
        # example, a failing import's cleanup step racing a successful
        # import's just-written output, or two stage updates racing each
        # other -- and could leave the run's `import` stage recorded
        # `complete` with no frozen artifact behind it. Acquiring this
        # run's lock for the full duration of the action (record read,
        # prerequisite check, artifact writes, engine invocation, and
        # stage update) makes every action on a given run id atomic with
        # respect to every other action on that same run id; actions on
        # *different* run ids are unaffected and still run concurrently.
        with self.app.lock_for_run(run_id):
            path, record = self.app.store.get(run_id)
            self._require_stage(record, action)
            extra: dict = {}
            record, extra = self._run_locked_action(run_id, action, body, path, record, extra)
            # Enumerate/hash artifacts *before* releasing this run's lock:
            # a concurrent action against the same run id (import's
            # stale-artifact cleanup, another dedup/lock invocation, etc.)
            # can delete or replace files on disk, and releasing the lock
            # first would let that race between the action just performed
            # above and the hashing of its resulting artifacts below,
            # producing either a spurious 500 (file vanished mid-hash) or a
            # response whose hashes belong to a different, later action's
            # output than the one this response reports on. Keeping
            # `store.artifacts()` inside the same `with` block makes the
            # whole action -- mutation and the artifact snapshot describing
            # it -- atomic with respect to every other action on this run.
            artifacts = self.app.store.artifacts(run_id)
        self._send_json({"run": record, "artifacts": artifacts, **extra})

    def _run_locked_action(
        self, run_id: str, action: str, body: dict, path: Path, record: dict, extra: dict
    ) -> tuple[dict, dict]:
        """The actual `doctor`/`import`/`dedup`/`lock`/`screen`/`package`/
        `replay`/`adjudicate`/`human_adjudication` action bodies, executed
        while `_run_action` holds this run's per-run lock. Kept as a
        separate method purely for readability; it is never called without
        that lock already held."""
        if action == "doctor":
            # `doctor` is a diagnostic, not a pass/fail bridge command: it
            # is allowed to report readiness problems (nonzero exit,
            # `passed: false`) without that being a WebUI-level error. The
            # action itself -- running the diagnostic and getting a
            # structured report back -- succeeded, so the run's `doctor`
            # stage is marked complete and the HTTP response is a normal
            # 200. The diagnostic's own failure information (passed, exit
            # code, stdout, stderr, and the full report) is preserved
            # verbatim in the response instead of being discarded, and
            # `run_diagnostic` still raises (surfacing as a 400 below) if
            # the engine did not actually produce a report at all.
            output_path = path / "doctor.json"
            diagnostic = self.app.bridge.run_diagnostic(
                [
                    "doctor",
                    "--repo-root",
                    str(self.app.bridge.engine_root),
                    "--skip-tests",
                    "--output",
                    str(output_path),
                ],
                output_path=output_path,
            )
            record = self.app.store.update(run_id, stage="doctor")
            extra["doctor"] = {
                "passed": diagnostic.passed,
                "exit_code": diagnostic.returncode,
                "stdout": diagnostic.stdout,
                "stderr": diagnostic.stderr,
                "report": diagnostic.report,
            }
        elif action == "import":
            # Source preservation first: the raw upload is written to disk
            # exactly as received *before* the engine is ever invoked, and a
            # failure from the engine below does not remove it. "Frozen
            # input" (docs/UI_BUILD_LOOP.md stage 2) means both the original
            # upload and the engine's frozen output are available afterward,
            # not that the raw upload is provisional on the engine
            # succeeding.
            filename = str(body.get("filename", ""))
            # The upload is transported as base64 text (JSON has no native
            # byte-string type), not as UTF-8-decoded text: decoding through
            # a text encoding before this point (as a prior version of this
            # handler did via `file.text()` in the browser and `.encode()`
            # here) would silently alter non-UTF-8 RIS/NBIB exports, invalid
            # byte sequences, and BOMs before they were ever persisted. This
            # base64 layer is purely a transport encoding: decoding it below
            # recovers the exact original bytes the operator selected.
            content_b64 = body.get("content_base64")
            if not isinstance(content_b64, str) or content_b64 == "":
                raise StoreError("import requires a non-empty 'content_base64' string")
            try:
                content_bytes = base64.b64decode(content_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise StoreError(f"import 'content_base64' must be valid base64: {exc}") from exc
            if not content_bytes:
                raise StoreError("import requires non-empty file content")
            safe_name = Path(filename).name
            if not safe_name or safe_name != filename or not _SAFE_FILENAME.fullmatch(safe_name):
                raise StoreError(
                    "import 'filename' must be a plain file name with no path segments"
                )
            suffix = Path(safe_name).suffix.lower()
            fmt = SUPPORTED_IMPORT_SUFFIXES.get(suffix)
            if fmt is None:
                raise StoreError(
                    "import only supports RIS (.ris) or NBIB (.nbib) source files, "
                    f"got '{filename}'"
                )
            source_dir = path / "source"
            source_dir.mkdir(exist_ok=True)
            source_path = source_dir / safe_name
            # write_bytes (not write_text, which can translate newlines or
            # re-encode through a text codec) so the on-disk artifact is
            # byte-for-byte identical to what the operator uploaded, for any
            # source encoding (UTF-8, Latin-1, Windows-1252, with or without
            # a BOM) -- this is the actual "source preservation" contract
            # stage 2 is required to verify. `content_bytes` above is the
            # base64-decoded original bytes, never routed through a text
            # decode/encode round trip.
            source_path.write_bytes(content_bytes)

            # Invalidate any output from a *previous* import before asking
            # the engine to run again: a stale frozen artifact (and raw
            # directory) left over from an earlier successful import must
            # never remain on disk -- and the run's `import` stage must
            # never still read `complete` -- if this re-import subsequently
            # fails. Both are reset up front, before the engine subprocess
            # is invoked, so a crash or nonzero exit below leaves neither
            # the stale artifact nor a falsely-`complete` stage behind.
            frozen_path = path / "frozen.json"
            raw_dir = path / "raw"
            dedup_path = path / "dedup.json"
            lock_path = path / "lock.json"
            screen_path = path / "screen.json"
            adjudications_path = path / "adjudications.json"
            decisions_path = path / "decisions.json"
            cache_dir = path / "cache"
            # A fresh import invalidates any "dedup"/"lock"/"screen"
            # artifacts and stage completions built on top of the
            # *previous* frozen output too: dedup deterministically
            # derives from frozen.json, lock deterministically derives
            # from dedup.json, and screen deterministically derives from
            # lock.json, so a changed frozen artifact makes all three
            # downstream artifacts (and any adjudications recorded against
            # the now-stale screen output) stale. Capture whether each was
            # complete (or, for "screen", complete-or-blocked -- either
            # means screening actually ran) *before* mutating any stage, so
            # the cascade below is based on the run's actual prior state,
            # not a state this same action has already partially
            # rewritten.
            had_dedup = record.get("stages", {}).get("dedup") == "complete"
            had_lock = record.get("stages", {}).get("lock") == "complete"
            had_screen = record.get("stages", {}).get("screen") in ("complete", "blocked")
            self._invalidate_package_and_replay(run_id, record, path)
            frozen_path.unlink(missing_ok=True)
            if raw_dir.exists():
                shutil.rmtree(raw_dir)
            dedup_path.unlink(missing_ok=True)
            lock_path.unlink(missing_ok=True)
            screen_path.unlink(missing_ok=True)
            adjudications_path.unlink(missing_ok=True)
            decisions_path.unlink(missing_ok=True)
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            self.app.store.update(run_id, stage="import", status="pending")
            if had_dedup:
                self.app.store.update(run_id, stage="dedup", status="pending")
            if had_lock:
                self.app.store.update(run_id, stage="lock", status="pending")
            if had_screen:
                self.app.store.update(run_id, stage="screen", status="pending")

            try:
                self.app.bridge.import_records(
                    source_path,
                    source_label=safe_name,
                    output_path=frozen_path,
                    raw_dir=raw_dir,
                )
                # `import_records`'s plain success/failure contract (see its
                # docstring) means a zero exit code is necessary but not
                # sufficient: it must also be checked against the frozen
                # artifact the engine is contracted to produce at
                # `frozen_path`. Without this check, an engine invocation
                # that exits 0 without writing `frozen.json` (for example a
                # CLI argument-shape drift, or a bug that no-ops instead of
                # erroring) would mark this run's "import" stage complete
                # with no frozen artifact behind it -- a false-success state
                # a later stage (dedup/lock) could then silently build on
                # top of. Treat that case the same as any other import
                # failure: raise so the `except Exception` branch below
                # removes any partial output and the failure propagates to
                # the operator instead of being reported as success.
                if not frozen_path.is_file():
                    raise BridgeError(
                        "import reported success but did not produce the expected "
                        f"frozen artifact at {frozen_path.name}"
                    )
            except Exception:
                # `import_records`'s contract is plain success/failure (see
                # bridge.py EngineBridge.import_records): a nonzero exit is
                # always a failure, even if the engine subprocess already
                # wrote a partial or fully-formed `frozen.json` and/or
                # `raw/` directory before exiting nonzero. Leaving those
                # outputs on disk on a failed import would violate the same
                # fail-closed guarantee already enforced above for a
                # *prior successful* import: the run would show a frozen
                # artifact for data the engine never actually finished
                # importing. Remove whatever the failed invocation left
                # behind before letting the failure propagate, so a failed
                # import never leaves misleading frozen artifacts on disk
                # -- only the preserved raw source upload written above
                # survives a failure.
                frozen_path.unlink(missing_ok=True)
                if raw_dir.exists():
                    shutil.rmtree(raw_dir)
                raise
            record = self.app.store.update(run_id, stage="import")
            extra["import"] = {
                "source": f"source/{safe_name}",
                "format": fmt,
            }
        elif action == "dedup":
            # `_require_stage` above already enforced that this run's
            # "import" stage is recorded complete before dispatching here,
            # but a recorded-complete stage is not itself proof the
            # artifact it depends on is still on disk (for example if the
            # run directory were tampered with out-of-band). Fail closed
            # with a clear message instead of letting the engine bridge
            # raise an opaque "file not found".
            frozen_path = path / "frozen.json"
            if not frozen_path.is_file():
                raise StoreError(
                    "dedup requires a frozen import artifact (frozen.json) on disk"
                )
            dedup_path = path / "dedup.json"
            lock_path = path / "lock.json"
            screen_path = path / "screen.json"
            adjudications_path = path / "adjudications.json"
            decisions_path = path / "decisions.json"
            cache_dir = path / "cache"
            # Same reasoning as the "import" branch's cascade above: a
            # fresh dedup output invalidates any "lock"/"screen" artifacts
            # and stage completions built on top of the *previous* dedup
            # output, because lock deterministically derives from
            # dedup.json and screen deterministically derives from
            # lock.json.
            had_lock = record.get("stages", {}).get("lock") == "complete"
            had_screen = record.get("stages", {}).get("screen") in ("complete", "blocked")
            self._invalidate_package_and_replay(run_id, record, path)
            dedup_path.unlink(missing_ok=True)
            lock_path.unlink(missing_ok=True)
            screen_path.unlink(missing_ok=True)
            adjudications_path.unlink(missing_ok=True)
            decisions_path.unlink(missing_ok=True)
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            self.app.store.update(run_id, stage="dedup", status="pending")
            if had_lock:
                self.app.store.update(run_id, stage="lock", status="pending")
            if had_screen:
                self.app.store.update(run_id, stage="screen", status="pending")

            try:
                self.app.bridge.dedup_records(frozen_path, output_path=dedup_path)
                # Same false-success guard as "import": a zero exit code is
                # necessary but not sufficient without the contracted
                # output actually present on disk.
                if not dedup_path.is_file():
                    raise BridgeError(
                        "dedup reported success but did not produce the expected "
                        f"artifact at {dedup_path.name}"
                    )
            except Exception:
                # Same fail-closed cleanup as "import": never leave a
                # partial/stale dedup artifact behind a failed dedup run.
                dedup_path.unlink(missing_ok=True)
                raise
            record = self.app.store.update(run_id, stage="dedup")
            extra["dedup"] = {
                "artifact": "dedup.json",
                "sha256": _sha256_file(dedup_path),
            }
        elif action == "lock":
            protocol_path = path / "protocol.json"
            procedure_path = path / "procedure.json"
            schema_path = path / "schema.json"
            dedup_path = path / "dedup.json"
            if not protocol_path.is_file():
                raise StoreError("lock requires the run's protocol artifact (protocol.json) on disk")
            if not procedure_path.is_file():
                raise StoreError("lock requires the run's procedure artifact (procedure.json) on disk")
            if not schema_path.is_file():
                raise StoreError("lock requires the run's schema artifact (schema.json) on disk")
            if not dedup_path.is_file():
                raise StoreError(
                    "lock requires a deduplication artifact (dedup.json) on disk"
                )
            lock_path = path / "lock.json"
            screen_path = path / "screen.json"
            adjudications_path = path / "adjudications.json"
            decisions_path = path / "decisions.json"
            cache_dir = path / "cache"
            # Same reasoning as "import"/"dedup"'s cascades above: a fresh
            # lock output invalidates any "screen" artifact and stage
            # completion (or blocked-on-adjudication state) built on top of
            # the *previous* lock output, because screen deterministically
            # derives from lock.json.
            had_screen = record.get("stages", {}).get("screen") in ("complete", "blocked")
            self._invalidate_package_and_replay(run_id, record, path)
            lock_path.unlink(missing_ok=True)
            screen_path.unlink(missing_ok=True)
            adjudications_path.unlink(missing_ok=True)
            decisions_path.unlink(missing_ok=True)
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            self.app.store.update(run_id, stage="lock", status="pending")
            if had_screen:
                self.app.store.update(run_id, stage="screen", status="pending")

            try:
                self.app.bridge.lock_protocol(
                    protocol_path,
                    procedure_path,
                    schema_path,
                    dedup_path,
                    output_path=lock_path,
                )
                if not lock_path.is_file():
                    raise BridgeError(
                        "lock reported success but did not produce the expected "
                        f"artifact at {lock_path.name}"
                    )
            except Exception:
                lock_path.unlink(missing_ok=True)
                raise
            record = self.app.store.update(run_id, stage="lock")
            extra["lock"] = {
                "artifact": "lock.json",
                "sha256": _sha256_file(lock_path),
            }
        elif action == "screen":
            # `_require_stage` above already enforced that this run's
            # "lock" stage is recorded complete before dispatching here,
            # but (same reasoning as "dedup"'s frozen.json check) that is
            # not itself proof the artifact it depends on is still on disk.
            lock_path = path / "lock.json"
            if not lock_path.is_file():
                raise StoreError("screen requires the run's lock artifact (lock.json) on disk")
            dedup_path = path / "dedup.json"
            if not dedup_path.is_file():
                raise StoreError("screen requires the run's deduplication artifact (dedup.json) on disk")
            screen_path = path / "screen.json"
            adjudications_path = path / "adjudications.json"
            decisions_path = path / "decisions.json"
            cache_dir = path / "cache"
            _ensure_offline_decisions(dedup_path, lock_path, decisions_path)
            # A fresh `screen` invocation invalidates any adjudications
            # recorded against a *previous* screen output: the blocking
            # queue's identity (which record ids are abstained) is entirely
            # a function of this run's cached screening output, so a
            # changed (or merely re-run) screen artifact makes any prior
            # human decisions against the old queue meaningless -- exactly
            # the same "re-running an earlier stage invalidates the
            # dependent artifact" posture stage 3 already established for
            # `dedup`/`lock`.
            self._invalidate_package_and_replay(run_id, record, path)
            screen_path.unlink(missing_ok=True)
            adjudications_path.unlink(missing_ok=True)
            self.app.store.update(run_id, stage="screen", status="pending")

            try:
                self.app.bridge.screen_records(
                    dedup_path,
                    lock_path,
                    decisions_path,
                    cache_dir,
                    human_adjudication=record.get("human_adjudication", "off"),
                    output_path=screen_path,
                )
                if not screen_path.is_file():
                    raise BridgeError(
                        "screen reported success but did not produce the expected "
                        f"artifact at {screen_path.name}"
                    )
                # `_screening_state` validates the freshly-written artifact's
                # shape (well-formed decisions, no duplicate/unknown/missing
                # record ids -- see its docstring) and must stay inside this
                # same try/except: it can be the thing that actually detects
                # a failed "screen" invocation (an engine output that exits
                # 0 but writes a malformed, duplicate, empty, incomplete, or
                # unknown-id `screen.json`). Validating it outside this block
                # would let that rejection propagate as a failed request
                # while leaving the invalid artifact it just rejected behind
                # on disk, where it would still appear in artifact listings
                # -- the same false-success gap already closed above for a
                # missing artifact.
                state = _screening_state(path)
            except Exception:
                screen_path.unlink(missing_ok=True)
                raise
            status = _screen_stage_status(record.get("human_adjudication", "off"), state["remaining"])
            record = self.app.store.update(run_id, stage="screen", status=status)
            extra["screen"] = {
                "artifact": "screen.json",
                "sha256": _sha256_file(screen_path),
                "decisions": state["decisions"],
                "abstained_ids": state["abstained_ids"],
                "remaining": state["remaining"],
                "blocked": status == "blocked",
            }
        elif action == "extract":
            screen_path = path / "screen.json"
            if not screen_path.is_file():
                raise StoreError("extract requires the run's screen artifact (screen.json) on disk")
            # A retry is a fresh extraction attempt. Invalidate any prior
            # record before validating request data so an invalid retry can
            # never leave stale extraction.json or a falsely complete stage.
            extraction_dir = path / "extraction"
            extraction_path = path / "extraction.json"
            extraction_path.unlink(missing_ok=True)
            if extraction_dir.exists():
                shutil.rmtree(extraction_dir)
            self._invalidate_rob_assessment(run_id, record, path)
            self.app.store.update(run_id, stage="extraction", status="pending")
            record_id = body.get("record_id")
            if not isinstance(record_id, str) or not record_id.strip():
                raise StoreError("extract requires a non-empty 'record_id' string")
            screening = _screening_state(path)
            decision_by_id = {
                entry["id"]: entry["decision"]
                for entry in screening["decisions"]
                if isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and isinstance(entry.get("decision"), str)
            }
            decision = decision_by_id.get(record_id)
            if decision is None:
                raise StoreError(
                    f"extract record_id '{record_id}' is not present in the completed screening result"
                )
            if decision == "abstain":
                adjudications = screening["adjudications"]
                decision = adjudications.get(record_id) if isinstance(adjudications, dict) else None
            if decision != "include":
                raise StoreError(
                    f"extract requires record_id '{record_id}' to have a final include decision"
                )
            content_b64 = body.get("content_base64")
            if not isinstance(content_b64, str) or not content_b64:
                raise StoreError("extract requires a non-empty 'content_base64' string")
            try:
                source_bytes = base64.b64decode(content_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise StoreError(f"extract 'content_base64' must be valid base64: {exc}") from exc
            if not source_bytes:
                raise StoreError("extract requires non-empty source content")
            try:
                source_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise StoreError("extract source content must be valid UTF-8 text") from exc
            filename = str(body.get("filename", "fulltext.txt"))
            safe_name = Path(filename).name
            if not safe_name or safe_name != filename or not _SAFE_FILENAME.fullmatch(safe_name):
                raise StoreError("extract 'filename' must be a plain file name with no path segments")
            fields = body.get("fields")
            if not isinstance(fields, list) or not fields:
                raise StoreError("extract requires a non-empty 'fields' array")
            if any(not isinstance(entry, dict) for entry in fields):
                raise StoreError("extract 'fields' entries must be JSON objects")

            source_dir = extraction_dir / "source"
            source_path = source_dir / safe_name
            fields_path = extraction_dir / "fields.json"
            source_dir.mkdir(parents=True, exist_ok=True)
            # Preserve the exact UTF-8 source bytes as the provenance anchor;
            # the engine reads this same file to create source spans.
            source_path.write_bytes(source_bytes)
            fields_path.write_text(
                json.dumps(fields, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            provenance_file = f"extraction/source/{safe_name}"
            try:
                self.app.bridge.extract_record(
                    source_path,
                    record_id=record_id,
                    provenance_file=provenance_file,
                    fields_path=fields_path,
                    output_path=extraction_path,
                )
                if not extraction_path.is_file():
                    raise BridgeError(
                        "extract reported success but did not produce the expected "
                        f"artifact at {extraction_path.name}"
                    )
                payload = _validate_extraction_artifact(
                    extraction_path,
                    source_bytes=source_bytes,
                    record_id=record_id,
                    provenance_file=provenance_file,
                    requested_fields=fields,
                )
            except Exception:
                # Keep the uploaded source and field specification available
                # for diagnosis, but never leave an invalid extraction record
                # that could be mistaken for a completed stage.
                extraction_path.unlink(missing_ok=True)
                raise
            record = self.app.store.update(run_id, stage="extraction")
            field_counts = {
                status: sum(1 for entry in payload["fields"] if entry["status"] == status)
                for status in sorted(_EXTRACTION_STATUSES)
            }
            extra["extraction"] = {
                "artifact": "extraction.json",
                "source": provenance_file,
                "sha256": _sha256_file(extraction_path),
                "record_hash": payload["record_hash"],
                "field_counts": field_counts,
            }
        elif action == "rob":
            extraction_path = path / "extraction.json"
            if not extraction_path.is_file():
                raise StoreError("rob requires the run's extraction artifact (extraction.json) on disk")
            extraction_payload = read_json(extraction_path)
            extraction_record_id = extraction_payload.get("record_id")
            if not isinstance(extraction_record_id, str) or not extraction_record_id.strip():
                raise StoreError("rob requires an extraction artifact with a valid record_id")
            required = {"record_id", "tool", "domains", "overall_judgment"}
            extra_keys = sorted(set(body) - required)
            if extra_keys:
                raise StoreError(f"rob has unsupported fields: {extra_keys!r}")
            record_id = body.get("record_id")
            tool = body.get("tool")
            domains = body.get("domains")
            overall_judgment = body.get("overall_judgment")
            if not isinstance(record_id, str) or not record_id.strip():
                raise StoreError("rob requires a non-empty 'record_id' string")
            if record_id != extraction_record_id:
                raise StoreError("rob record_id must match the extracted included record")
            if not isinstance(tool, str) or not tool.strip():
                raise StoreError("rob requires a non-empty 'tool' string")
            if not isinstance(domains, list) or not domains:
                raise StoreError("rob requires a non-empty 'domains' array")
            if overall_judgment not in _ROB_JUDGMENTS:
                raise StoreError(f"rob has unsupported overall_judgment: {overall_judgment!r}")
            normalized_domains: list[dict[str, object]] = []
            for entry in domains:
                if not isinstance(entry, dict):
                    raise StoreError("rob domain entries must be JSON objects")
                allowed = {"domain", "judgment", "rationale", "source_links"}
                unsupported = sorted(set(entry) - allowed)
                if unsupported:
                    raise StoreError(f"rob domain has unsupported fields: {unsupported!r}")
                normalized_domains.append(
                    {
                        "domain": entry.get("domain"),
                        "judgment": entry.get("judgment"),
                        "rationale": entry.get("rationale"),
                        "source_links": entry.get("source_links", []),
                    }
                )
            spec = {
                "record_id": record_id,
                "tool": tool,
                "domains": normalized_domains,
                "overall_judgment": overall_judgment,
            }
            rob_dir = path / "rob"
            rob_path = path / "rob.json"
            self._invalidate_rob_assessment(run_id, record, path)
            rob_dir.mkdir(parents=True, exist_ok=True)
            spec_path = rob_dir / "spec.json"
            spec_path.write_text(
                json.dumps(spec, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.app.store.update(run_id, stage="rob", status="pending")
            try:
                self.app.bridge.rob_assessment(spec_path, output_path=rob_path)
                if not rob_path.is_file():
                    raise BridgeError(
                        "rob reported success but did not produce the expected "
                        f"artifact at {rob_path.name}"
                    )
                payload = _validate_rob_artifact(
                    rob_path,
                    requested_spec=spec,
                    extraction_record_id=extraction_record_id,
                )
            except Exception:
                rob_path.unlink(missing_ok=True)
                raise
            record = self.app.store.update(run_id, stage="rob")
            extra["rob"] = {
                "artifact": "rob.json",
                "sha256": _sha256_file(rob_path),
                "rob_hash": payload["rob_hash"],
                "record_id": payload["record_id"],
                "overall_judgment": payload["overall_judgment"],
                "domain_count": len(payload["domains"]),
            }
        elif action == "synthesize":
            extraction_path = path / "extraction.json"
            rob_path = path / "rob.json"
            if not extraction_path.is_file():
                raise StoreError("synthesize requires the run's extraction artifact (extraction.json) on disk")
            if not rob_path.is_file():
                raise StoreError("synthesize requires the run's risk-of-bias artifact (rob.json) on disk")
            extraction_payload = read_json(extraction_path)
            rob_payload = read_json(rob_path)
            extraction_record_id = extraction_payload.get("record_id")
            if not isinstance(extraction_record_id, str) or not extraction_record_id.strip():
                raise StoreError("synthesize requires an extraction artifact with a valid record_id")
            if rob_payload.get("record_id") != extraction_record_id:
                raise StoreError("synthesize risk-of-bias record_id does not match extraction")
            required = {"record_id", "measure", "counts"}
            unsupported = sorted(set(body) - required)
            if unsupported:
                raise StoreError(f"synthesize has unsupported fields: {unsupported!r}")
            record_id = body.get("record_id")
            measure = body.get("measure")
            counts = body.get("counts")
            if not isinstance(record_id, str) or not record_id.strip():
                raise StoreError("synthesize requires a non-empty 'record_id' string")
            if record_id != extraction_record_id:
                raise StoreError("synthesize record_id must match the extracted included record")
            if measure not in _SYNTHESIS_MEASURES:
                raise StoreError(f"synthesize has unsupported measure: {measure!r}")
            if not isinstance(counts, dict) or set(counts) != set(_SYNTHESIS_COUNTS):
                raise StoreError(
                    "synthesize counts must contain exactly treated_events, treated_total, "
                    "control_events, and control_total"
                )
            spec = {"record_id": record_id, "measure": measure, "counts": counts}
            synthesis_dir = path / "synthesis"
            synthesis_path = path / "synthesis.json"
            self._invalidate_synthesis(run_id, record, path)
            synthesis_dir.mkdir(parents=True, exist_ok=True)
            spec_path = synthesis_dir / "spec.json"
            spec_path.write_text(
                json.dumps(spec, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.app.store.update(run_id, stage="synthesis", status="pending")
            try:
                self.app.bridge.synthesize(spec_path, output_path=synthesis_path)
                if not synthesis_path.is_file():
                    raise BridgeError(
                        "synthesize reported success but did not produce the expected "
                        f"artifact at {synthesis_path.name}"
                    )
                payload = _validate_synthesis_artifact(
                    synthesis_path,
                    requested_spec=spec,
                    extraction_record_id=extraction_record_id,
                )
            except Exception:
                synthesis_path.unlink(missing_ok=True)
                raise
            record = self.app.store.update(run_id, stage="synthesis")
            extra["synthesis"] = {
                "artifact": "synthesis.json",
                "sha256": _sha256_file(synthesis_path),
                "calculation_hash": payload["calculation_hash"],
                "record_id": payload["record_id"],
                "measure": payload["measure"],
                "status": payload["status"],
                "uncertainty_flags": payload["uncertainty_flags"],
            }
        elif action == "package":
            # `_require_stage` above already enforced that this run's
            # "screen" stage is recorded complete before dispatching here
            # (see ACTION_PREREQUISITES' comment), but -- same reasoning as
            # every other prerequisite check in this method -- that is not
            # itself proof the four artifacts a package bundles are still on
            # disk (for example if the run directory were tampered with
            # out-of-band). Fail closed with a clear message instead of
            # letting the engine bridge raise an opaque "file not found".
            missing_inputs = [
                name for name in PACKAGE_ARTIFACT_NAMES if not (path / name).is_file()
            ]
            if missing_inputs:
                raise StoreError(
                    f"package requires the run's {missing_inputs} artifact(s) on disk"
                )
            package_path = path / "package.json"
            # Re-running "package" invalidates any "replay" artifact/stage
            # built on top of the *previous* package output, since replay
            # deterministically reproduces package.json -- the same cascade
            # posture stage 3/4 already established one stage further
            # upstream.
            had_replay = record.get("stages", {}).get("replay") in ("complete", "mismatched")
            replay_dir = path / "replay"
            package_path.unlink(missing_ok=True)
            if replay_dir.exists():
                shutil.rmtree(replay_dir)
            self.app.store.update(run_id, stage="package", status="pending")
            if had_replay:
                self.app.store.update(run_id, stage="replay", status="pending")

            try:
                self.app.bridge.package_run(path / "screen.json", output_path=package_path)
                if not package_path.is_file():
                    raise BridgeError(
                        "package reported success but did not produce the expected "
                        f"artifact at {package_path.name}"
                    )
                # `_read_package_manifest` validates the freshly-written
                # artifact's shape (a `manifest` object covering exactly
                # `PACKAGE_ARTIFACT_NAMES`, each a well-formed sha256 hex
                # string) and must stay inside this same try/except for the
                # same reason `_screening_state` does inside "screen"'s try
                # block above: it can be the thing that actually detects a
                # failed "package" invocation (an engine output that exits 0
                # but writes a malformed manifest). It is not, by itself,
                # proof the manifest's recorded digests actually match this
                # run's current on-disk artifacts -- checked explicitly
                # below -- so a package whose manifest is well-formed but
                # wrong (for example computed against a stale copy) is still
                # caught here rather than silently trusted.
                manifest = _read_package_manifest(path)
                mismatched_inputs = [
                    name
                    for name in PACKAGE_ARTIFACT_NAMES
                    if manifest[name] != _sha256_file(path / name)
                ]
                if mismatched_inputs:
                    raise BridgeError(
                        "package artifact manifest does not match this run's current "
                        f"on-disk artifact(s): {mismatched_inputs}"
                    )
            except Exception:
                # Same fail-closed cleanup as every other pipeline action:
                # never leave a partial/stale/invalid package artifact
                # behind a failed (or false-success, or manifest-mismatched)
                # package run.
                package_path.unlink(missing_ok=True)
                raise
            record = self.app.store.update(run_id, stage="package")
            extra["package"] = {
                "artifact": "package.json",
                "sha256": _sha256_file(package_path),
                "manifest": manifest,
            }
        elif action == "replay":
            # `_require_stage` above already enforced that this run's
            # "package" stage is recorded complete before dispatching here,
            # but (same reasoning as "package"'s own input check just
            # above) that is not itself proof the package artifact -- or the
            # four artifacts it claims to describe -- are still accurate.
            # `_read_package_manifest` fails closed if `package.json` is
            # missing or malformed; the explicit mismatch check below fails
            # closed if it is well-formed but stale (for example an earlier
            # stage was somehow re-run without going through this handler's
            # own invalidation cascade).
            manifest = _read_package_manifest(path)
            stale_inputs = [
                name
                for name in PACKAGE_ARTIFACT_NAMES
                if not (path / name).is_file() or manifest[name] != _sha256_file(path / name)
            ]
            if stale_inputs:
                raise StoreError(
                    "replay requires this run's package manifest to match its current "
                    f"on-disk artifact(s); stale or missing: {stale_inputs} (re-run "
                    "'package' first)"
                )
            replay_dir = path / "replay"
            if replay_dir.exists():
                shutil.rmtree(replay_dir)
            self.app.store.update(run_id, stage="replay", status="pending")

            try:
                self.app.bridge.replay_run(
                    path / "package.json",
                    lock_path=path / "lock.json",
                    dedup_path=path / "dedup.json",
                    cache_dir=path / "cache",
                    output_dir=replay_dir,
                )
                missing_outputs = [
                    name for name in PACKAGE_ARTIFACT_NAMES if not (replay_dir / name).is_file()
                ]
                if missing_outputs:
                    raise BridgeError(
                        "replay reported success but did not reproduce the expected "
                        f"artifact(s) under replay/: {missing_outputs}"
                    )
            except Exception:
                # A genuine engine execution failure (nonzero exit, or a
                # zero exit that never actually reproduced every artifact)
                # is not evidence about byte-stability at all -- there is
                # nothing meaningful to audit -- so, unlike a byte
                # *mismatch* (handled below, which is kept on disk as audit
                # evidence), this cleans up any partial replay output.
                if replay_dir.exists():
                    shutil.rmtree(replay_dir)
                raise
            result = _replay_comparison(path, manifest)
            # A byte mismatch is not a bridge execution failure: the engine
            # ran successfully and produced a complete, well-formed replay
            # output, it just does not reproduce the original bytes. This is
            # exactly the "verify byte-stable offline replay" contract this
            # stage exists to check, so it is reported (not raised) and the
            # non-matching replay output is deliberately left on disk as
            # audit evidence for the operator to inspect -- the run's
            # "replay" stage reads "mismatched" (neither "complete" nor
            # "pending") so it is never mistaken for a successful replay.
            status = "complete" if result["byte_stable"] else "mismatched"
            record = self.app.store.update(run_id, stage="replay", status=status)
            extra["replay"] = {"artifact_dir": "replay", **result}
        elif action == "adjudicate":
            # "adjudicate" has no entry in ACTION_PREREQUISITES (see that
            # map's comment): its gate is more specific than "a stage is
            # complete" -- the run's "screen" stage must currently read
            # exactly "blocked" (an open blocking queue), not "pending" (no
            # screening has run yet) and not "complete" (nothing is
            # blocking, so there is nothing to adjudicate).
            stages = record.get("stages", {})
            screen_stage = stages.get("screen", "pending")
            if screen_stage != "blocked":
                raise StageOrderError(
                    "'adjudicate' requires the run's 'screen' stage to be blocked on a "
                    f"pending human adjudication (current status: {screen_stage})"
                )
            record_id = body.get("id")
            decision = body.get("decision")
            if not isinstance(record_id, str) or not record_id:
                raise StoreError("adjudicate requires a non-empty 'id' string")
            if not isinstance(decision, str) or decision not in ADJUDICATION_DECISION_VALUES:
                raise StoreError(
                    "adjudicate 'decision' must be one of "
                    f"{sorted(ADJUDICATION_DECISION_VALUES)}, got {decision!r}"
                )
            state = _screening_state(path)
            if record_id not in state["abstained_ids"]:
                raise StoreError(
                    f"'{record_id}' is not part of this run's screening blocking queue"
                )
            adjudications = dict(state["adjudications"])
            adjudications[record_id] = decision
            _write_adjudications(path, adjudications)
            remaining = [rid for rid in state["abstained_ids"] if rid not in adjudications]
            status = _screen_stage_status(record.get("human_adjudication", "off"), remaining)
            record = self.app.store.update(run_id, stage="screen", status=status)
            extra["adjudicate"] = {
                "id": record_id,
                "decision": decision,
                "remaining": remaining,
                "blocked": status == "blocked",
            }
        elif action == "human_adjudication":
            # An explicit, always-available on/off toggle
            # (docs/UI_BUILD_LOOP.md stage 4): unlike every other action
            # here, this never invokes the engine bridge at all -- it only
            # records a run setting -- and it has no prerequisite (see
            # ACTION_PREREQUISITES' comment): an operator may flip it
            # before a run has ever been screened.
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise StoreError("human_adjudication requires a JSON boolean 'enabled' field")
            new_value = "on" if enabled else "off"
            screen_path = path / "screen.json"
            if screen_path.is_file():
                # Re-evaluate the run's existing screening output against
                # the *new* setting immediately, including retroactively:
                # turning adjudication on can newly block a run whose
                # screen stage previously read "complete" only because
                # adjudication was off, and turning it off discharges an
                # existing block without requiring each remaining queue
                # entry to be individually adjudicated.
                state = _screening_state(path)
                status = _screen_stage_status(new_value, state["remaining"])
                previous_screen_status = record.get("stages", {}).get("screen")
                if previous_screen_status == "complete" and status != "complete":
                    # Turning adjudication on can demote this run's
                    # previously-"complete" "screen" stage to "blocked"
                    # against an existing unresolved queue, without this
                    # branch ever touching screen.json itself. "package"
                    # requires "screen" to read complete before it can run
                    # (see ACTION_PREREQUISITES), so any package/replay
                    # artifact and stage completion already on disk was
                    # built on top of that now-stale "screen complete"
                    # state and must be invalidated by the same cascade
                    # every other branch above applies when it re-runs an
                    # earlier stage -- otherwise a run could keep reporting
                    # a "complete" package and a byte-stable replay while
                    # its prerequisite screening stage now reads "blocked".
                    # Called with `record` captured before this branch's
                    # own `stage="screen"` update just below, exactly like
                    # the existing had_dedup/had_lock/had_screen cascade
                    # checks `_invalidate_package_and_replay` documents.
                    self._invalidate_package_and_replay(run_id, record, path)
                record = self.app.store.update(
                    run_id, human_adjudication=new_value, stage="screen", status=status
                )
            else:
                record = self.app.store.update(run_id, human_adjudication=new_value)
            extra["human_adjudication"] = {"enabled": enabled, "value": new_value}
        else:  # pragma: no cover - guarded by the VALID_ACTIONS check in _run_action
            raise AssertionError(f"unreachable: unsupported action {action!r}")
        return record, extra

    def _invalidate_package_and_replay(self, run_id: str, record: dict, path: Path) -> None:
        """Invalidate this run's downstream artifacts and stage
        completions, mirroring stage 3's/stage 4's re-run invalidation
        cascade one stage further downstream: `package` deterministically
        bundles `frozen.json`, `dedup.json`,
        `lock.json`, and `screen.json` (see `PACKAGE_ARTIFACT_NAMES`), and
        `replay` deterministically reproduces `package.json`, and
        `extraction.json` is bound to the screen-stage population. Therefore
        re-running any earlier stage (`import`, `dedup`, `lock`, or `screen`)
        makes all downstream artifacts and stage completions stale. Called
        from each of those four branches below, using the `record` state
        captured *before* this same action call's own stage mutations."""
        package_path = path / "package.json"
        replay_dir = path / "replay"
        stages = record.get("stages", {})
        had_package = stages.get("package") == "complete"
        had_replay = stages.get("replay") in ("complete", "mismatched")
        had_extraction = stages.get("extraction") == "complete"
        package_path.unlink(missing_ok=True)
        if replay_dir.exists():
            shutil.rmtree(replay_dir)
        extraction_path = path / "extraction.json"
        extraction_dir = path / "extraction"
        extraction_path.unlink(missing_ok=True)
        if extraction_dir.exists():
            shutil.rmtree(extraction_dir)
        self._invalidate_rob_assessment(run_id, record, path)
        if had_package:
            self.app.store.update(run_id, stage="package", status="pending")
        if had_replay:
            self.app.store.update(run_id, stage="replay", status="pending")
        if had_extraction:
            self.app.store.update(run_id, stage="extraction", status="pending")

    def _invalidate_rob_assessment(self, run_id: str, record: dict, path: Path) -> None:
        """Remove a risk-of-bias result bound to an older extraction."""
        rob_path = path / "rob.json"
        rob_dir = path / "rob"
        rob_path.unlink(missing_ok=True)
        if rob_dir.exists():
            shutil.rmtree(rob_dir)
        if record.get("stages", {}).get("rob") == "complete":
            self.app.store.update(run_id, stage="rob", status="pending")
        self._invalidate_synthesis(run_id, record, path)

    def _invalidate_synthesis(self, run_id: str, record: dict, path: Path) -> None:
        """Remove a synthesis result bound to an older RoB/extraction state."""
        synthesis_path = path / "synthesis.json"
        synthesis_dir = path / "synthesis"
        synthesis_path.unlink(missing_ok=True)
        if synthesis_dir.exists():
            shutil.rmtree(synthesis_dir)
        if record.get("stages", {}).get("synthesis") == "complete":
            self.app.store.update(run_id, stage="synthesis", status="pending")

    @staticmethod
    def _require_stage(record: dict, action: str) -> None:
        """Enforce that an action's prerequisite stage is recorded complete
        on this run before dispatching it to the engine bridge. This is the
        actual stage gate: it consults the run's persisted `stages` map
        rather than relying on the engine failing on a missing input file."""
        prerequisite = ACTION_PREREQUISITES.get(action)
        if prerequisite is None:
            return
        stages = record.get("stages", {})
        if stages.get(prerequisite) != "complete":
            raise StageOrderError(
                f"'{action}' requires the '{prerequisite}' stage to be complete first "
                f"(current status: {stages.get(prerequisite, 'pending')})"
            )


class WebUIApp:
    def __init__(self, engine_root: Path, data_root: Path) -> None:
        self.store = RunStore(data_root)
        self.bridge = EngineBridge(engine_root)
        # One lock per run id, created lazily and reused for the lifetime
        # of this process. `ThreadingHTTPServer` runs each request on its
        # own thread, so without this, two actions dispatched concurrently
        # for the same run id could interleave their file writes, engine
        # subprocess execution, and stage updates (see
        # WebUIHandler._run_action for the concrete failure mode this
        # prevents). `_run_locks_guard` only ever protects the brief
        # get-or-create on `_run_locks` itself, never the action bodies
        # that then acquire the per-run lock it returns.
        self._run_locks: dict[str, threading.Lock] = {}
        self._run_locks_guard = threading.Lock()

    def lock_for_run(self, run_id: str) -> threading.Lock:
        """Return the lock serializing all actions dispatched for
        `run_id`, creating it on first use. The lock is process-lifetime
        and never removed, which is intentional: the number of distinct
        run ids created by this local, single-operator tool over one
        process lifetime is small and bounded by disk space long before
        the lock table would matter."""
        with self._run_locks_guard:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._run_locks[run_id] = lock
            return lock

    def health(self) -> dict:
        # credentials_loaded/credentials_isolated reflect the same
        # allowlisted, HOME-redirected environment the bridge actually
        # passes to engine subprocesses (bridge.py
        # EngineBridge.subprocess_environment), not a static claim. They
        # report on that constructed subprocess environment specifically:
        # no credential-shaped variable names are forwarded, and
        # HOME/USERPROFILE/APPDATA/LOCALAPPDATA are redirected to an empty
        # scratch directory rather than the host's real value (see
        # bridge.py EngineBridge for why the latter matters). This is
        # environment isolation for a same-user local subprocess, not an
        # OS-level sandbox guarantee that no credential material anywhere on
        # disk is reachable.
        isolated = self.bridge.credentials_isolated()
        return {
            "ok": self.bridge.engine_root.is_dir(),
            "offline_only": True,
            "engine_root": str(self.bridge.engine_root),
            "live_connectors": False,
            "credentials_loaded": not isolated,
            "credentials_isolated": isolated,
            "engine_env_allowlist": sorted(self.bridge.ALLOWED_ENV_KEYS),
        }


def create_server(host: str = "127.0.0.1", port: int = 8787, *, engine_root: Path | None = None, data_root: Path | None = None) -> ThreadingHTTPServer:
    configured_engine = engine_root or Path(os.environ.get("ESE_ENGINE_ROOT", "/Users/neurologic/Projects/evidence-synthesis-engine-screen-prove"))
    configured_data = data_root or Path(os.environ.get("ESE_UI_DATA_ROOT", str(Path.home() / ".ese-screen-prove-ui")))
    server = ThreadingHTTPServer((host, port), WebUIHandler)
    server.app = WebUIApp(configured_engine, configured_data)  # type: ignore[attr-defined]
    return server


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the local offline ESE WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    app_server = create_server(args.host, args.port)
    print(f"ESE WebUI: http://{args.host}:{args.port}", flush=True)
    app_server.serve_forever()
