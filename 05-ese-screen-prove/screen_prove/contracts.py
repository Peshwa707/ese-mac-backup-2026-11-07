"""Hashable contracts and run-envelope integrity checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "task-envelope-v1"


def canonical_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for hashes and byte-stable artifacts."""
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def object_hash(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


class IntegrityError(ValueError):
    """Raised when a frozen artifact cannot be verified."""


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


@dataclass(frozen=True)
class ProtocolLock:
    protocol: dict[str, Any]
    procedure: dict[str, Any]
    schema: dict[str, Any]
    input_hash: str
    version: str = "protocol-lock-v1"
    protocol_hash: str = field(init=False)
    procedure_hash: str = field(init=False)
    schema_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol_hash", object_hash(self.protocol))
        object.__setattr__(self, "procedure_hash", object_hash(self.procedure))
        object.__setattr__(self, "schema_hash", object_hash(self.schema))
        if not _is_sha256(self.input_hash):
            raise IntegrityError("input_hash must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "protocol": self.protocol,
            "procedure": self.procedure,
            "schema": self.schema,
            "input_hash": self.input_hash,
            "protocol_hash": self.protocol_hash,
            "procedure_hash": self.procedure_hash,
            "schema_hash": self.schema_hash,
            "lock_hash": object_hash({
                "version": self.version,
                "protocol_hash": self.protocol_hash,
                "procedure_hash": self.procedure_hash,
                "schema_hash": self.schema_hash,
                "input_hash": self.input_hash,
            }),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProtocolLock":
        lock = cls(
            protocol=raw["protocol"],
            procedure=raw["procedure"],
            schema=raw["schema"],
            input_hash=raw["input_hash"],
            version=raw.get("version", "protocol-lock-v1"),
        )
        for key in ("protocol_hash", "procedure_hash", "schema_hash"):
            if raw.get(key) != getattr(lock, key):
                raise IntegrityError(f"{key} does not match locked content")
        expected = lock.to_dict()["lock_hash"]
        if raw.get("lock_hash") != expected:
            raise IntegrityError("lock_hash does not match locked content")
        return lock


@dataclass(frozen=True)
class TaskEnvelope:
    protocol_hash: str
    procedure_hash: str
    schema_hash: str
    input_hash: str
    adapter: str
    adapter_version: str
    provider: str
    model: str
    decoding: dict[str, Any]
    request_hash: str
    response_hash: str
    attestation: str = "offline-fixture"
    version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskEnvelope":
        envelope = cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})
        envelope.validate()
        return envelope

    def validate(self) -> None:
        for name in ("protocol_hash", "procedure_hash", "schema_hash", "input_hash", "request_hash", "response_hash"):
            value = getattr(self, name)
            if not _is_sha256(value):
                raise IntegrityError(f"{name} must be a SHA-256 hex digest")
        if self.version != SCHEMA_VERSION:
            raise IntegrityError(f"unsupported task envelope version: {self.version}")


def write_json(path: Path, value: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(canonical_bytes(value))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
