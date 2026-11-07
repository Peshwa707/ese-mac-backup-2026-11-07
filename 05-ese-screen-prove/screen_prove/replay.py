"""Offline package writing and end-to-end replay verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import IntegrityError, ProtocolLock, object_hash, read_json, write_json
from .screening import _response_hash, _read_cache


def write_package(path: Path, package: dict[str, Any]) -> Path:
    body = {key: value for key, value in package.items() if key != "package_hash"}
    artifact = {**body, "package_hash": object_hash(body)}
    write_json(Path(path), artifact)
    return Path(path)


def _verified_package(path: Path) -> dict[str, Any]:
    try:
        raw = read_json(path)
    except Exception as exc:
        raise IntegrityError(f"invalid package artifact: {path}") from exc
    if not isinstance(raw, dict):
        raise IntegrityError("package artifact must be an object")
    body = {key: value for key, value in raw.items() if key != "package_hash"}
    if raw.get("package_hash") != object_hash(body):
        raise IntegrityError("package_hash does not match package content")
    return body


def verify_replay(package_path: Path, lock: ProtocolLock, dedup: dict[str, Any], cache_dir: Path) -> bool:
    package = _verified_package(Path(package_path))
    if package.get("version") != "screening-package-v1":
        raise IntegrityError("unsupported screening package version")
    if package.get("human_adjudication") not in {"off", "on"}:
        raise IntegrityError("invalid package human_adjudication mode")
    manifest = package.get("manifest") or {}
    expected_lock_hash = lock.to_dict()["lock_hash"]
    if manifest.get("lock_hash") != expected_lock_hash or manifest.get("corpus_hash") != lock.input_hash:
        raise IntegrityError("package does not match protocol lock")
    if dedup.get("corpus_hash") != lock.input_hash:
        raise IntegrityError("deduplicated input does not match protocol lock")
    if dedup.get("records_hash") != object_hash({"records": dedup.get("records", [])}):
        raise IntegrityError("records_hash does not match deduplicated records")
    records = {record["record_id"]: record for record in dedup.get("records", [])}
    package_ids = {str(row.get("record_id")) for row in package.get("results", [])}
    if package_ids != set(records):
        raise IntegrityError("package record coverage does not match deduplicated input")
    adapter = manifest.get("adapter")
    if not isinstance(adapter, dict):
        raise IntegrityError("package adapter manifest is missing")
    for row in package.get("results", []):
        record = records.get(row.get("record_id"))
        if record is None:
            raise IntegrityError(f"package record is not in deduplicated input: {row.get('record_id')}")
        envelope = row.get("envelope") or {}
        request = {
            "record": record,
            "lock_hash": expected_lock_hash,
            "human_adjudication": package["human_adjudication"],
            **adapter,
        }
        request_hash = object_hash(request)
        if envelope.get("request_hash") != request_hash:
            raise IntegrityError(f"request_hash mismatch for {row.get('record_id')}")
        cache_path = Path(cache_dir) / f"{request_hash}.json"
        response = _read_cache(cache_path, request_hash)
        if response["decision"] != row.get("decision") or response["reason"] != row.get("reason"):
            raise IntegrityError(f"screening result mismatch for {row.get('record_id')}")
        if envelope.get("response_hash") != _response_hash(response):
            raise IntegrityError(f"response_hash mismatch for {row.get('record_id')}")
    return True
