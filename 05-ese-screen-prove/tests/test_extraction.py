import pytest

from screen_prove.contracts import IntegrityError, canonical_bytes, object_hash, sha256_bytes
from screen_prove.extraction import (
    build_extraction_record,
    estimated_field,
    extract_field,
    not_reported_field,
    verify_extraction_record,
    verify_span_against_source,
)

FULL_TEXT = "Sample size was 240 participants. Adverse events were not tabulated."
SOURCE_HASH = sha256_bytes(FULL_TEXT.encode("utf-8"))
PROVENANCE_FILE = "abcdef0123456789-fulltext.txt"


# --- extract_field (reported, source-span-backed) ---------------------------


def test_extract_field_reads_the_exact_span_as_the_value():
    field = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    assert field["status"] == "reported"
    assert field["value"] == "240"
    assert field["span"]["quote"] == "240"
    assert field["span"]["source_hash"] == SOURCE_HASH
    assert field["span"]["provenance_file"] == PROVENANCE_FILE


def test_extract_field_rejects_source_hash_mismatch():
    with pytest.raises(IntegrityError, match="source_hash"):
        extract_field(FULL_TEXT, "a" * 64, PROVENANCE_FILE, "sample_size", 16, 19)


def test_extract_field_rejects_an_out_of_range_span():
    with pytest.raises(IntegrityError, match="out of range"):
        extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 10, 1000)


def test_extract_field_rejects_an_empty_span():
    with pytest.raises(IntegrityError, match="empty"):
        extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 5, 5)


def test_extract_field_rejects_a_whitespace_only_span():
    assert FULL_TEXT[6:7] == " "
    with pytest.raises(IntegrityError, match="empty"):
        extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 6, 7)


# --- not_reported_field (NR) -------------------------------------------------


def test_not_reported_field_carries_no_value_or_span():
    field = not_reported_field("blinding_method")
    assert field["status"] == "not_reported"
    assert field["value"] == ""
    assert field["span"] is None


# --- estimated_field ----------------------------------------------------------


def test_estimated_field_requires_a_calculation_note():
    with pytest.raises(IntegrityError, match="estimation_note"):
        estimated_field("attrition_rate", "0.12", "")


def test_estimated_field_requires_a_non_empty_value():
    with pytest.raises(IntegrityError, match="value"):
        estimated_field("attrition_rate", "", "12/100 dropped out")


def test_estimated_field_records_the_calculation():
    field = estimated_field("attrition_rate", "0.12", "12/100 dropped out")
    assert field["status"] == "estimated"
    assert field["value"] == "0.12"
    assert field["estimation_note"] == "12/100 dropped out"
    assert field["span"] is None


# --- build_extraction_record / verify_extraction_record --------------------


def _record():
    fields = [
        extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19),
        not_reported_field("blinding_method"),
        estimated_field("attrition_rate", "0.12", "12/100 dropped out"),
    ]
    return build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, fields)


def test_build_extraction_record_is_byte_stable_for_identical_inputs():
    record_a = _record()
    record_b = _record()
    assert canonical_bytes(record_a) == canonical_bytes(record_b)


def test_verify_extraction_record_accepts_a_valid_record():
    record = _record()
    verified = verify_extraction_record(record)
    assert verified["record_id"] == "record:1"
    assert len(verified["fields"]) == 3


def test_build_extraction_record_rejects_empty_record_id():
    with pytest.raises(IntegrityError, match="record_id"):
        build_extraction_record("", SOURCE_HASH, PROVENANCE_FILE, [not_reported_field("x")])


def test_build_extraction_record_rejects_duplicate_field_names():
    fields = [not_reported_field("blinding_method"), not_reported_field("blinding_method")]
    with pytest.raises(IntegrityError, match="duplicate"):
        build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, fields)


def test_build_extraction_record_rejects_a_span_from_a_different_source():
    other_hash = sha256_bytes(b"a different frozen document")
    tampered = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    tampered["span"]["source_hash"] = other_hash
    with pytest.raises(IntegrityError, match="source_hash"):
        build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, [tampered])


def test_build_extraction_record_rejects_a_span_from_a_different_provenance_file():
    tampered = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    tampered["span"]["provenance_file"] = "some-other-file.txt"
    with pytest.raises(IntegrityError, match="provenance_file"):
        build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, [tampered])


def test_build_extraction_record_rejects_an_empty_source_hash():
    fields = [not_reported_field("blinding_method")]
    with pytest.raises(IntegrityError, match="source_hash"):
        build_extraction_record("record:1", "", PROVENANCE_FILE, fields)


def test_build_extraction_record_rejects_an_empty_provenance_file():
    fields = [not_reported_field("blinding_method")]
    with pytest.raises(IntegrityError, match="provenance_file"):
        build_extraction_record("record:1", SOURCE_HASH, "", fields)


def test_build_extraction_record_rejects_a_non_string_provenance_file():
    fields = [not_reported_field("blinding_method")]
    with pytest.raises(IntegrityError, match="provenance_file"):
        build_extraction_record("record:1", SOURCE_HASH, None, fields)  # type: ignore[arg-type]


def test_build_extraction_record_rejects_a_not_reported_field_with_a_value():
    bad = {"field": "blinding_method", "status": "not_reported", "value": "double-blind", "span": None, "estimation_note": ""}
    with pytest.raises(IntegrityError, match="not_reported"):
        build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, [bad])


def test_build_extraction_record_rejects_a_reported_field_whose_value_is_not_in_its_quote():
    bad = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    bad["value"] = "9999"
    with pytest.raises(IntegrityError, match="does not exactly match"):
        build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, [bad])


def test_build_extraction_record_rejects_a_fabricated_value_that_is_merely_a_substring_of_the_quote():
    # "40" is a substring of the true quote "240" but was never itself read
    # from the source; a reported value must equal the quote exactly.
    bad = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    assert bad["span"]["quote"] == "240"
    bad["value"] = "40"
    with pytest.raises(IntegrityError, match="does not exactly match"):
        build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, [bad])


def test_build_extraction_record_rejects_a_not_reported_field_with_a_non_string_value():
    for non_empty_value in (123, ["double-blind"], {"note": "double-blind"}):
        bad = {
            "field": "blinding_method",
            "status": "not_reported",
            "value": non_empty_value,
            "span": None,
            "estimation_note": "",
        }
        with pytest.raises(IntegrityError, match="not_reported"):
            build_extraction_record("record:1", SOURCE_HASH, PROVENANCE_FILE, [bad])


def test_verify_extraction_record_rejects_tampering_after_the_fact():
    record = _record()
    record["fields"][0]["value"] = "9999999"
    with pytest.raises(IntegrityError, match="record_hash"):
        verify_extraction_record(record)


def test_verify_extraction_record_rejects_duplicate_fields_with_a_recomputed_hash():
    # Construct a record with duplicate field names directly (bypassing
    # build_extraction_record's own uniqueness check) and recompute the
    # record_hash over that tampered body, so this exercises the
    # replay-time uniqueness check itself, not just the hash guard.
    record = dict(_record())
    record["fields"] = [not_reported_field("blinding_method"), not_reported_field("blinding_method")]
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="duplicate"):
        verify_extraction_record(record)


def test_verify_extraction_record_rejects_an_empty_record_id_with_a_recomputed_hash():
    record = dict(_record())
    record["record_id"] = ""
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="record_id"):
        verify_extraction_record(record)


def test_verify_extraction_record_rejects_a_span_provenance_file_mismatch_with_a_recomputed_hash():
    # Tamper with the span's provenance_file after construction and
    # recompute record_hash over the tampered body, so this exercises the
    # replay-time span/record provenance agreement check itself, not just
    # the hash-mismatch guard in front of it.
    record = dict(_record())
    record["fields"] = [dict(field) for field in record["fields"]]
    record["fields"][0] = dict(record["fields"][0])
    record["fields"][0]["span"] = dict(record["fields"][0]["span"])
    record["fields"][0]["span"]["provenance_file"] = "some-other-file.txt"
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="provenance_file"):
        verify_extraction_record(record)


def test_verify_extraction_record_rejects_an_empty_provenance_file_with_a_recomputed_hash():
    record = dict(_record())
    record["provenance_file"] = ""
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="provenance_file"):
        verify_extraction_record(record)


def test_verify_extraction_record_rejects_an_empty_source_hash_with_a_recomputed_hash():
    record = dict(_record())
    record["source_hash"] = ""
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="source_hash"):
        verify_extraction_record(record)


def test_verify_extraction_record_rejects_an_unsupported_version_with_a_recomputed_hash():
    # Recompute record_hash over the tampered body so this exercises the
    # version check itself, not just the hash-mismatch guard in front of it.
    record = dict(_record())
    record["version"] = "extraction-record-v0"
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="unsupported extraction record version"):
        verify_extraction_record(record)


# --- verify_span_against_source (tamper checks against the frozen source) --


def test_verify_span_against_source_accepts_a_matching_span():
    field = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    verify_span_against_source(field["span"], FULL_TEXT)


def test_verify_span_against_source_rejects_a_changed_source_document():
    field = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    changed_text = FULL_TEXT.replace("240", "999")
    with pytest.raises(IntegrityError, match="source_hash"):
        verify_span_against_source(field["span"], changed_text)


def test_verify_span_against_source_rejects_a_quote_that_no_longer_matches_the_offsets():
    field = extract_field(FULL_TEXT, SOURCE_HASH, PROVENANCE_FILE, "sample_size", 16, 19)
    field["span"]["quote"] = "999"
    with pytest.raises(IntegrityError, match="quote"):
        verify_span_against_source(field["span"], FULL_TEXT)
