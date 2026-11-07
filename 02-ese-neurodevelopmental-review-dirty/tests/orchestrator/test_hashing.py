"""WP-M1.2 — content hashing + staleness."""
from core.sr.orchestrator import hashing as h
from core.sr.orchestrator.state import StageState


def test_hash_bytes_deterministic():
    assert h.hash_bytes(b"abc") == h.hash_bytes(b"abc")
    assert h.hash_bytes(b"abc") != h.hash_bytes(b"abd")
    assert len(h.hash_bytes(b"abc")) == 64


def test_hash_json_key_order_invariant():
    assert h.hash_json({"a": 1, "b": 2}) == h.hash_json({"b": 2, "a": 1})
    assert h.hash_json({"a": 1}) != h.hash_json({"a": 2})


def test_hash_json_nested_and_unicode():
    a = {"outer": {"b": 2, "a": 1}, "list": [{"y": "café"}, {"x": "naïve"}]}
    b = {"list": [{"y": "café"}, {"x": "naïve"}], "outer": {"a": 1, "b": 2}}
    assert h.hash_json(a) == h.hash_json(b)              # order-invariant, unicode-stable
    assert h.hash_json({"k": "café"}) != h.hash_json({"k": "cafe"})


def test_hash_json_rejects_non_string_keys():
    import pytest
    with pytest.raises(TypeError):
        h.hash_json({1: "a"})
    with pytest.raises(TypeError):
        h.hash_json({"ok": {2: "nested"}})


def test_hash_file_matches_bytes(tmp_path):
    p = tmp_path / "art.json"
    p.write_bytes(b'{"x": 1}')
    assert h.hash_file(p) == h.hash_bytes(b'{"x": 1}')


def test_is_stale_false_when_unchanged():
    s = StageState(name="extract", input_hashes={"corpus.json": "h1"})
    assert h.is_stale(s, {"corpus.json": "h1"}) is False


def test_is_stale_true_on_changed_upstream():
    s = StageState(name="extract", input_hashes={"corpus.json": "h1"})
    assert h.is_stale(s, {"corpus.json": "h2"}) is True


def test_is_stale_true_when_upstream_missing():
    s = StageState(name="extract", input_hashes={"corpus.json": "h1"})
    assert h.is_stale(s, {}) is True


def test_is_stale_false_with_no_recorded_inputs():
    assert h.is_stale(StageState(name="search"), {"anything": "h"}) is False
