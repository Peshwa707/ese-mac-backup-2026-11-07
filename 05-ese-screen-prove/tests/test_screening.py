import json

import pytest

from screen_prove.contracts import IntegrityError, ProtocolLock, object_hash
from screen_prove.screening import StaticDecisionAdapter, build_package, screen_records


def _lock(corpus_hash="c" * 64):
    return ProtocolLock({"include": ["RCT"]}, {"temperature": 0}, {"decision": "enum"}, corpus_hash)


def _dedup(lock):
    result = {
        "version": "dedup-v1",
        "source_hash": "a" * 64,
        "corpus_hash": lock.input_hash,
        "records": [
            {"record_id": "r1", "title": "Alpha", "abstract": "A"},
            {"record_id": "r2", "title": "Beta", "abstract": "B"},
            {"record_id": "r3", "title": "Gamma", "abstract": "C"},
        ],
        "decisions": [],
        "counts": {"identified": 3, "after_dedup": 3, "duplicates_removed": 0},
    }
    result["records_hash"] = object_hash({"records": result["records"]})
    return result


def test_ai_only_screening_preserves_abstain_and_writes_cache(tmp_path):
    lock = _lock()
    dedup = _dedup(lock)
    adapter = StaticDecisionAdapter({"r1": "include", "r2": "abstain"})

    first = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")
    second = screen_records(dedup, lock, adapter, tmp_path / "cache", human_adjudication="off")

    assert [row["decision"] for row in first["results"]] == ["include", "abstain", "abstain"]
    assert first == second
    assert len(list((tmp_path / "cache").glob("*.json"))) == 3
    assert all(row["envelope"]["version"] == "task-envelope-v1" for row in first["results"])


def test_human_on_creates_blocking_queue_for_abstentions(tmp_path):
    lock = _lock()
    screening = screen_records(
        _dedup(lock), lock, StaticDecisionAdapter({"r1": "include", "r2": "abstain"}),
        tmp_path / "cache", human_adjudication="on",
    )

    package = build_package(screening)

    assert package["package_type"] == "HUMAN_ADJUDICATION_PACKAGE"
    assert package["governed_export_allowed"] is False
    assert package["adjudication_queue"] == {"unresolved_record_ids": ["r2", "r3"], "blocking": True}


def test_human_off_always_emits_ai_only_package(tmp_path):
    lock = _lock()
    screening = screen_records(
        _dedup(lock), lock, StaticDecisionAdapter({"r1": "include"}),
        tmp_path / "cache", human_adjudication="off",
    )

    package = build_package(screening)

    assert package["package_type"] == "AI_ONLY_SCREENING_PACKAGE"
    assert package["governed_export_allowed"] is False
    assert package["adjudication_queue"]["blocking"] is False


def test_screening_rejects_cache_tampering(tmp_path):
    lock = _lock()
    dedup = _dedup(lock)
    cache_dir = tmp_path / "cache"
    screen_records(dedup, lock, StaticDecisionAdapter({"r1": "include"}), cache_dir, human_adjudication="off")
    cache_file = next(cache_dir.glob("*.json"))
    raw = json.loads(cache_file.read_text(encoding="utf-8"))
    raw["response"]["decision"] = "exclude"
    cache_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(IntegrityError, match="response_hash"):
        screen_records(dedup, lock, StaticDecisionAdapter({"r1": "include"}), cache_dir, human_adjudication="off")


def test_screening_rejects_edited_dedup_records(tmp_path):
    lock = _lock()
    dedup = _dedup(lock)
    dedup["records"][0]["title"] = "Edited after dedup"

    with pytest.raises(IntegrityError, match="records_hash"):
        screen_records(dedup, lock, StaticDecisionAdapter({}), tmp_path / "cache", human_adjudication="off")


def test_screening_normalizes_corrupt_cache_error(tmp_path):
    lock = _lock()
    dedup = _dedup(lock)
    cache_dir = tmp_path / "cache"
    screen_records(dedup, lock, StaticDecisionAdapter({"r1": "include"}), cache_dir, human_adjudication="off")
    next(cache_dir.glob("*.json")).write_text("not json", encoding="utf-8")

    with pytest.raises(IntegrityError, match="cache"):
        screen_records(dedup, lock, StaticDecisionAdapter({"r1": "include"}), cache_dir, human_adjudication="off")


def test_screening_rejects_invalid_human_adjudication_mode(tmp_path):
    lock = _lock()
    with pytest.raises(ValueError, match="human_adjudication"):
        screen_records(_dedup(lock), lock, StaticDecisionAdapter({}), tmp_path / "cache", human_adjudication="maybe")
