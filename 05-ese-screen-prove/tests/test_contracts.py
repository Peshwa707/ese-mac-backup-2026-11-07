from pathlib import Path

import pytest

from screen_prove.contracts import IntegrityError, ProtocolLock, TaskEnvelope, canonical_bytes, object_hash
from screen_prove.models import Reference


def test_canonical_bytes_are_order_independent():
    assert canonical_bytes({"b": 2, "a": 1}) == canonical_bytes({"a": 1, "b": 2})


def test_protocol_lock_hashes_each_locked_component():
    lock = ProtocolLock({"eligibility": ["RCT"]}, {"temperature": 0}, {"decision": "enum"}, "a" * 64)
    raw = lock.to_dict()
    assert raw["protocol_hash"] == object_hash(lock.protocol)
    assert ProtocolLock.from_dict(raw).to_dict() == raw


def test_protocol_lock_rejects_tampering():
    lock = ProtocolLock({"eligibility": ["RCT"]}, {}, {}, "a" * 64)
    raw = lock.to_dict()
    raw["protocol"]["eligibility"].append("cohort")
    with pytest.raises(IntegrityError, match="protocol_hash"):
        ProtocolLock.from_dict(raw)


def test_task_envelope_rejects_non_sha_hashes():
    with pytest.raises(IntegrityError, match="request_hash"):
        TaskEnvelope("a" * 64, "b" * 64, "c" * 64, "d" * 64, "static", "1", "offline", "fixture", {}, "bad", "e" * 64)


def test_protocol_lock_rejects_non_hex_input_hash():
    with pytest.raises(IntegrityError, match="input_hash"):
        ProtocolLock({}, {}, {}, "z" * 64)


def test_canonical_bytes_reject_nan():
    with pytest.raises(ValueError):
        canonical_bytes({"score": float("nan")})


def test_reference_defaults_preserve_field_types():
    ref = Reference.from_dict({"record_id": "r1", "title": "Title"})
    assert ref.raw_index == 0
    assert ref.title == "Title"
