"""One offline cached screening adapter and the adjudication-mode boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol
import json

from .contracts import IntegrityError, ProtocolLock, TaskEnvelope, object_hash, read_json, write_json
from .models import AdjudicationQueue, ScreenDecision, ScreenResult, ScreeningPackage


class ScreeningAdapter(Protocol):
    name: str
    version: str
    provider: str
    model: str
    decoding: dict[str, Any]

    def decide(self, record: Mapping[str, Any]) -> tuple[ScreenDecision, str]: ...


class StaticDecisionAdapter:
    """Offline fixture adapter; missing decisions become explicit abstentions."""

    name = "static-fixture"
    version = "1"
    provider = "offline"
    model = "fixture"
    decoding: dict[str, Any] = {"temperature": 0}

    def __init__(self, decisions: Mapping[str, str | ScreenDecision]):
        self.decisions = {key: ScreenDecision(value) for key, value in decisions.items()}

    def decide(self, record: Mapping[str, Any]) -> tuple[ScreenDecision, str]:
        decision = self.decisions.get(str(record["record_id"]), ScreenDecision.ABSTAIN)
        reason = "static fixture decision" if str(record["record_id"]) in self.decisions else "no decision supplied"
        return decision, reason


def _adapter_meta(adapter: ScreeningAdapter) -> dict[str, Any]:
    return {
        "adapter": adapter.name,
        "adapter_version": adapter.version,
        "provider": adapter.provider,
        "model": adapter.model,
        "decoding": adapter.decoding,
    }


def _response_hash(response: dict[str, str]) -> str:
    return object_hash(response)


def _read_cache(cache_path: Path, request_hash: str) -> dict[str, str]:
    try:
        raw = read_json(cache_path)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise IntegrityError(f"invalid cache artifact: {cache_path}") from exc
    if not isinstance(raw, dict):
        raise IntegrityError(f"invalid cache artifact: {cache_path}")
    if raw.get("request_hash") != request_hash:
        raise IntegrityError(f"request_hash mismatch in cache: {cache_path}")
    response = raw.get("response")
    if not isinstance(response, dict) or raw.get("response_hash") != _response_hash(response):
        raise IntegrityError(f"response_hash mismatch in cache: {cache_path}")
    try:
        ScreenDecision(response["decision"])
    except (KeyError, ValueError) as exc:
        raise IntegrityError(f"invalid cached screening decision: {cache_path}") from exc
    return {"decision": response["decision"], "reason": str(response.get("reason", ""))}


def screen_records(
    dedup: dict[str, Any],
    lock: ProtocolLock,
    adapter: ScreeningAdapter,
    cache_dir: Path,
    *,
    human_adjudication: str,
) -> dict[str, Any]:
    if human_adjudication not in {"off", "on"}:
        raise ValueError("human_adjudication must be 'off' or 'on'")
    if dedup.get("corpus_hash") != lock.input_hash:
        raise IntegrityError("screening input does not match protocol lock")
    if dedup.get("records_hash") != object_hash({"records": dedup.get("records", [])}):
        raise IntegrityError("records_hash does not match deduplicated records")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta = _adapter_meta(adapter)
    results: list[dict[str, Any]] = []
    for record in dedup.get("records", []):
        request = {
            "record": record,
            "lock_hash": lock.to_dict()["lock_hash"],
            "human_adjudication": human_adjudication,
            **meta,
        }
        request_hash = object_hash(request)
        cache_path = cache_dir / f"{request_hash}.json"
        if cache_path.exists():
            response = _read_cache(cache_path, request_hash)
        else:
            decision, reason = adapter.decide(record)
            response = {"decision": ScreenDecision(decision).value, "reason": reason}
            write_json(cache_path, {
                "version": "screen-cache-v1",
                "request_hash": request_hash,
                "response": response,
                "response_hash": _response_hash(response),
            })
        envelope = TaskEnvelope(
            protocol_hash=lock.protocol_hash,
            procedure_hash=lock.procedure_hash,
            schema_hash=lock.schema_hash,
            input_hash=lock.input_hash,
            adapter=adapter.name,
            adapter_version=adapter.version,
            provider=adapter.provider,
            model=adapter.model,
            decoding=adapter.decoding,
            request_hash=request_hash,
            response_hash=_response_hash(response),
        )
        results.append({
            "record_id": record["record_id"],
            "decision": response["decision"],
            "reason": response["reason"],
            "envelope": envelope.to_dict(),
        })
    return {
        "version": "screening-v1",
        "corpus_hash": lock.input_hash,
        "lock_hash": lock.to_dict()["lock_hash"],
        "human_adjudication": human_adjudication,
        "adapter": meta,
        "results": results,
    }


def build_package(screening: dict[str, Any]) -> dict[str, Any]:
    mode = screening.get("human_adjudication")
    if mode not in {"off", "on"}:
        raise IntegrityError("screening artifact has invalid human_adjudication mode")
    abstained = [row["record_id"] for row in screening.get("results", []) if row.get("decision") == ScreenDecision.ABSTAIN.value]
    queue = AdjudicationQueue(
        unresolved_record_ids=tuple(abstained if mode == "on" else []),
        blocking=bool(abstained) and mode == "on",
    )
    if mode == "off":
        package_type = "AI_ONLY_SCREENING_PACKAGE"
        governed = False
    else:
        package_type = "HUMAN_ADJUDICATION_PACKAGE"
        governed = not abstained
    results = tuple(
        ScreenResult(
            record_id=row["record_id"],
            decision=ScreenDecision(row["decision"]),
            reason=row["reason"],
            envelope=row["envelope"],
        )
        for row in screening.get("results", [])
    )
    return ScreeningPackage(
        package_type=package_type,
        human_adjudication=mode,
        governed_export_allowed=governed,
        results=results,
        adjudication_queue=queue,
        manifest={
            "corpus_hash": screening["corpus_hash"],
            "lock_hash": screening["lock_hash"],
            "adapter": screening["adapter"],
        },
    ).to_dict()
