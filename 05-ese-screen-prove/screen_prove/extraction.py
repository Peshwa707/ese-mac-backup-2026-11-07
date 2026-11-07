"""Frozen full-text/reference extraction records with source spans and
`NR`/`ESTIMATED` handling.

Converts a frozen full-text (or reference) source into auditable extraction
records. Every field is exactly one of three bounded statuses:

- `reported` -- backed by an exact, byte-verifiable source-text span; the
  value is read directly from the frozen text, never typed or inferred.
- `not_reported` (`NR`) -- the frozen source does not state this field; it
  carries no value and no span.
- `estimated` -- a calculated value that must carry an explicit
  `estimation_note` describing the calculation, and never a source span (an
  estimated value is not something the source text itself reports).

Every reported field's span is bound to a `source_hash` computed from the
exact frozen full text, and every record carries a `record_hash` over its
own content. `verify_extraction_record` and `verify_span_against_source`
detect tampering: a later change to a stored record or to the frozen source
text it was extracted from is always caught, never silently accepted.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import IntegrityError, _is_sha256, object_hash, sha256_bytes

EXTRACTION_RECORD_VERSION = "extraction-record-v1"
EXTRACTION_STATUSES = ("reported", "not_reported", "estimated")


def extract_field(
    full_text: str,
    source_hash: str,
    provenance_file: str,
    field: str,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Build one `reported` extraction field from an exact source-text span.

    The quote and value are read directly from `full_text[start:end]`;
    nothing is typed or inferred by the caller. Raises `IntegrityError` if
    `source_hash` does not match `full_text`, if the span is out of range,
    or if the span is empty or whitespace-only (it cannot back a reported
    value).
    """
    if sha256_bytes(full_text.encode("utf-8")) != source_hash:
        raise IntegrityError(f"{field}: source_hash does not match the supplied full text")
    if not (0 <= start <= end <= len(full_text)):
        raise IntegrityError(f"{field}: source span [{start}, {end}) is out of range")
    quote = full_text[start:end]
    if not quote.strip():
        raise IntegrityError(f"{field}: source span is empty or whitespace-only")
    return {
        "field": field,
        "status": "reported",
        "value": quote.strip(),
        "span": {
            "source_hash": source_hash,
            "provenance_file": provenance_file,
            "start": start,
            "end": end,
            "quote": quote,
        },
        "estimation_note": "",
    }


def not_reported_field(field: str) -> dict[str, Any]:
    """Build one `not_reported` (`NR`) extraction field: no value, no span."""
    return {"field": field, "status": "not_reported", "value": "", "span": None, "estimation_note": ""}


def estimated_field(field: str, value: str, estimation_note: str) -> dict[str, Any]:
    """Build one `estimated` extraction field.

    Requires a non-empty computed `value` and a non-empty `estimation_note`
    that names the calculation performed; never carries a source span.
    """
    if not value.strip():
        raise IntegrityError(f"{field}: estimated field requires a non-empty value")
    if not estimation_note.strip():
        raise IntegrityError(f"{field}: estimated field requires an estimation_note")
    return {
        "field": field,
        "status": "estimated",
        "value": value.strip(),
        "span": None,
        "estimation_note": estimation_note.strip(),
    }


def _validate_field(entry: Mapping[str, Any], source_hash: str, provenance_file: str) -> None:
    field = entry.get("field")
    if not isinstance(field, str) or not field.strip():
        raise IntegrityError("extraction field entry is missing a non-empty 'field' name")
    status = entry.get("status")
    if status not in EXTRACTION_STATUSES:
        raise IntegrityError(f"{field}: unknown extraction status {status!r}")
    value = entry.get("value", "")
    span = entry.get("span")
    if status == "reported":
        if not isinstance(value, str) or not value.strip():
            raise IntegrityError(f"{field}: reported field requires a non-empty value")
        if not isinstance(span, Mapping):
            raise IntegrityError(f"{field}: reported field requires a source span")
        required_keys = ("source_hash", "provenance_file", "start", "end", "quote")
        if any(key not in span for key in required_keys):
            raise IntegrityError(f"{field}: source span is missing required keys")
        if span.get("source_hash") != source_hash:
            raise IntegrityError(f"{field}: span source_hash does not match record source_hash")
        if span.get("provenance_file") != provenance_file:
            raise IntegrityError(f"{field}: span provenance_file does not match record provenance_file")
        quote = span.get("quote")
        if not isinstance(quote, str) or value.strip() != quote.strip():
            raise IntegrityError(f"{field}: reported value does not exactly match its source span quote")
        start, end = span.get("start"), span.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
        ):
            raise IntegrityError(f"{field}: source span start/end must be integer offsets")
        if not (0 <= start <= end):
            raise IntegrityError(
                f"{field}: source span start/end must be non-negative and ordered (start <= end)"
            )
        if end - start != len(quote):
            raise IntegrityError(f"{field}: source span start/end does not match the quote length")
    elif status == "not_reported":
        if value != "" or span is not None:
            raise IntegrityError(f"{field}: not_reported field must not carry a value or source span")
    else:  # estimated
        if not isinstance(value, str) or not value.strip():
            raise IntegrityError(f"{field}: estimated field requires a non-empty value")
        if span is not None:
            raise IntegrityError(f"{field}: estimated field must not carry a source span")
        note = entry.get("estimation_note", "")
        if not isinstance(note, str) or not note.strip():
            raise IntegrityError(f"{field}: estimated field requires an estimation_note")


def build_extraction_record(
    record_id: str,
    source_hash: str,
    provenance_file: str,
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble a byte-stable, auditable extraction record from field entries.

    Requires `source_hash` to be a SHA-256 hex digest and a non-empty
    `provenance_file` for the record itself. Every field entry is
    validated: `reported` fields must carry a source span whose
    `source_hash` and `provenance_file` exactly match this record's own
    `source_hash` and `provenance_file`, whose quote exactly matches the
    reported value, and whose `start`/`end` are non-negative, ordered
    integer offsets whose difference equals the quote length;
    `not_reported` fields must carry neither value (the canonical empty
    string `""`) nor span; `estimated` fields must carry a value and an
    `estimation_note` and never a source span. Field names must be unique
    within one record. The returned record carries a `record_hash` binding
    its exact content; `verify_extraction_record` detects any later change.
    """
    if not record_id.strip():
        raise IntegrityError("extraction record requires a non-empty record_id")
    if not isinstance(source_hash, str) or not _is_sha256(source_hash):
        raise IntegrityError("extraction record requires source_hash to be a SHA-256 hex digest")
    if not isinstance(provenance_file, str) or not provenance_file.strip():
        raise IntegrityError("extraction record requires a non-empty provenance_file")
    seen: set[str] = set()
    normalized_fields: list[dict[str, Any]] = []
    for entry in fields:
        _validate_field(entry, source_hash, provenance_file)
        name = entry["field"]
        if name in seen:
            raise IntegrityError(f"duplicate extraction field: {name}")
        seen.add(name)
        normalized_fields.append(dict(entry))
    body = {
        "version": EXTRACTION_RECORD_VERSION,
        "record_id": record_id,
        "source_hash": source_hash,
        "provenance_file": provenance_file,
        "fields": normalized_fields,
    }
    return {**body, "record_hash": object_hash(body)}


def verify_extraction_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a stored extraction record's integrity and re-validate every field.

    Raises `IntegrityError` if the record's `record_hash` does not match its
    content (tamper detection), if the record version is unsupported, if
    `record_id` or `provenance_file` is missing, empty, or not a string, if
    `source_hash` is not a SHA-256 hex digest, if `fields` is missing or not
    a list, if any field entry is not a mapping, if any field violates the
    `reported`/`not_reported`/`estimated` invariants (including a
    `reported` field whose span `source_hash` or `provenance_file` does not
    exactly agree with the record's own `source_hash`/`provenance_file`, or
    whose span `start`/`end` are not non-negative, ordered integer offsets
    consistent with the quote length), or if any field name is duplicated
    -- the same construction-time invariants enforced by
    `build_extraction_record` are re-checked here, not just recomputed.
    """
    body = {key: value for key, value in record.items() if key != "record_hash"}
    if record.get("record_hash") != object_hash(body):
        raise IntegrityError("record_hash does not match extraction record content")
    if body.get("version") != EXTRACTION_RECORD_VERSION:
        raise IntegrityError(f"unsupported extraction record version: {body.get('version')}")
    record_id = body.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise IntegrityError("extraction record requires a non-empty record_id")
    source_hash = body.get("source_hash")
    if not isinstance(source_hash, str) or not _is_sha256(source_hash):
        raise IntegrityError("extraction record requires source_hash to be a SHA-256 hex digest")
    provenance_file = body.get("provenance_file")
    if not isinstance(provenance_file, str) or not provenance_file.strip():
        raise IntegrityError("extraction record requires a non-empty provenance_file")
    fields = body.get("fields")
    if not isinstance(fields, list):
        raise IntegrityError("extraction record requires a 'fields' list")
    seen: set[str] = set()
    for entry in fields:
        if not isinstance(entry, Mapping):
            raise IntegrityError("extraction field entry must be a mapping")
        _validate_field(entry, source_hash, provenance_file)
        name = entry["field"]
        if name in seen:
            raise IntegrityError(f"duplicate extraction field: {name}")
        seen.add(name)
    return body


def verify_span_against_source(span: Mapping[str, Any], full_text: str) -> None:
    """Verify one source span's quote and hash against the frozen full text.

    This is the tamper check that binds a stored span back to the original
    frozen source text: it fails if the source text has changed since
    extraction, or if the span's offsets no longer produce the recorded
    quote.
    """
    if sha256_bytes(full_text.encode("utf-8")) != span.get("source_hash"):
        raise IntegrityError("source span source_hash does not match the supplied full text")
    start, end = span.get("start"), span.get("end")
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(full_text)):
        raise IntegrityError("source span offsets are out of range")
    if full_text[start:end] != span.get("quote"):
        raise IntegrityError("source span quote does not match the supplied full text")
