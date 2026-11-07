import json

import pytest

from screen_prove.contracts import IntegrityError, ProtocolLock, object_hash
from screen_prove.evaluation import compare_package, seal_labels, verify_sealed_labels
from screen_prove.replay import verify_replay, write_package
from screen_prove.screening import StaticDecisionAdapter, build_package, screen_records


def _lock(corpus_hash="c" * 64):
    return ProtocolLock({"eligibility": ["RCT"]}, {"temperature": 0}, {"decision": "enum"}, corpus_hash)


def _dedup(lock):
    records = [
        {"record_id": "r1", "title": "Alpha"},
        {"record_id": "r2", "title": "Beta"},
    ]
    return {
        "version": "dedup-v1",
        "source_hash": "a" * 64,
        "corpus_hash": lock.input_hash,
        "records": records,
        "records_hash": object_hash({"records": records}),
        "decisions": [],
        "counts": {"identified": 2, "after_dedup": 2, "duplicates_removed": 0},
    }


def _package(tmp_path, mode="off"):
    lock = _lock()
    screening = screen_records(
        _dedup(lock), lock, StaticDecisionAdapter({"r1": "include", "r2": "abstain"}),
        tmp_path / "cache", human_adjudication=mode,
    )
    return lock, _dedup(lock), screening, build_package(screening)


def test_sealed_labels_compare_without_revealing_labels_to_screening(tmp_path):
    lock, dedup, screening, package = _package(tmp_path)
    sealed = seal_labels({"r1": "include", "r2": "exclude", "r9": "include"}, tmp_path / "labels.json")

    report = compare_package(package, verify_sealed_labels(sealed))

    assert report == {
        "matched": 1,
        "disagreements": 0,
        "ai_abstentions": 1,
        "missing_published_label": 0,
        "missing_ai_result": 1,
        "published_label_count": 3,
    }


def test_sealed_labels_reject_tampering(tmp_path):
    sealed = seal_labels({"r1": "include"}, tmp_path / "labels.json")
    sealed["labels"]["r1"] = "exclude"

    with pytest.raises(IntegrityError, match="labels_hash"):
        verify_sealed_labels(sealed)


def test_sealed_labels_reject_non_object(tmp_path):
    path = tmp_path / "bad-labels.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(IntegrityError, match="artifact"):
        verify_sealed_labels(path)


def test_package_write_and_replay_are_byte_stable(tmp_path):
    lock, dedup, screening, package = _package(tmp_path)
    package_path = write_package(tmp_path / "package.json", package)

    assert verify_replay(package_path, lock, dedup, tmp_path / "cache") is True
    first_bytes = package_path.read_bytes()
    write_package(package_path, package)
    assert package_path.read_bytes() == first_bytes


def test_replay_rejects_package_tampering(tmp_path):
    lock, dedup, screening, package = _package(tmp_path)
    package_path = write_package(tmp_path / "package.json", package)
    raw = json.loads(package_path.read_text(encoding="utf-8"))
    raw["results"][0]["decision"] = "exclude"
    package_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(IntegrityError, match="package_hash"):
        verify_replay(package_path, lock, dedup, tmp_path / "cache")


def test_compare_rejects_tampered_package(tmp_path):
    lock, dedup, screening, package = _package(tmp_path)
    package_path = write_package(tmp_path / "package.json", package)
    sealed_path = tmp_path / "labels.json"
    seal_labels({"r1": "include", "r2": "exclude"}, sealed_path)
    raw = json.loads(package_path.read_text(encoding="utf-8"))
    raw["results"][0]["decision"] = "exclude"
    package_path.write_text(json.dumps(raw), encoding="utf-8")

    from screen_prove.cli import main
    with pytest.raises(IntegrityError, match="package_hash"):
        main(["compare", str(package_path), "--labels", str(sealed_path), "--output", str(tmp_path / "report.json")])


def test_replay_rejects_package_missing_a_dedup_record(tmp_path):
    lock, dedup, screening, package = _package(tmp_path)
    package["results"] = package["results"][:1]
    package_path = write_package(tmp_path / "package.json", package)

    with pytest.raises(IntegrityError, match="record coverage"):
        verify_replay(package_path, lock, dedup, tmp_path / "cache")
