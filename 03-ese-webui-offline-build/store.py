"""Local run and artifact storage for the first offline WebUI package."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    """Return the hex-encoded sha256 digest of a file's exact on-disk
    bytes, read in fixed-size chunks. Shared by `RunStore.artifacts()`
    below; kept private to this module rather than imported from
    `server.py` so `store.py` has no dependency on the HTTP layer."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


RUN_ID = re.compile(r"^[a-z0-9]{12}$")


class StoreError(ValueError):
    """Raised for invalid or missing local run data."""


class StageOrderError(StoreError):
    """Raised when an action is invoked before its prerequisite stage is complete."""


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _run_dir(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise StoreError("invalid run id")
        path = (self.root / run_id).resolve()
        if path.parent != self.root or not path.is_dir():
            raise StoreError("run not found")
        return path

    def create(self, name: str) -> dict[str, Any]:
        clean_name = " ".join(name.split()).strip()
        if not clean_name or len(clean_name) > 120:
            raise StoreError("run name must contain 1-120 non-whitespace characters")
        while True:
            run_id = secrets.token_hex(6)
            path = self.root / run_id
            if not path.exists():
                break
        path.mkdir()
        record = {
            "id": run_id,
            "name": clean_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "human_adjudication": "off",
            "stages": {
                "initialized": "pending",
                "import": "pending",
                "dedup": "pending",
                "lock": "pending",
                "screen": "pending",
                "package": "pending",
                "replay": "pending",
                "extraction": "pending",
                "rob": "pending",
                "synthesis": "pending",
            },
            "last_error": None,
        }
        self.write_record(path, record)
        return record

    def write_record(self, run_dir: Path, record: dict[str, Any]) -> None:
        (run_dir / "run.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def get(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._run_dir(run_id)
        record_path = path / "run.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"invalid run manifest: {exc}") from exc
        if not isinstance(record, dict):
            raise StoreError("invalid run manifest")
        return path, record

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and RUN_ID.fullmatch(path.name):
                try:
                    records.append(self.get(path.name)[1])
                except StoreError:
                    continue
        return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """List every persisted artifact for a run with its name, byte
        size, and content hash.

        This started as the stage-1 operator-shell contract
        (docs/UI_BUILD_LOOP.md stage 1: "create/select a run, show
        offline-only status, and list artifacts") -- a basic listing an
        operator can use to see what the engine has produced for a run so
        far. Stage 3 ("deduplication and lock") adds the `sha256` field:
        every artifact's exact on-disk content is now hashed, so an
        operator (or a test) can verify an artifact's bytes deterministically
        -- for example that two dedup runs against the same frozen input
        produce byte-identical output, or that a lock artifact really does
        correspond to the protocol/dedup bytes it was produced from --
        without re-reading and diffing full artifact bodies by hand.
        Byte-for-byte artifact *inspection* (rendering full artifact content
        back to the operator) is a separate, stage-5 read route
        (`server.py` `_artifact_content`, reachable via
        `GET /api/runs/<id>/artifacts/<name>`) and intentionally not part of
        this listing.
        """
        path, _ = self.get(run_id)
        result = []
        for item in sorted(path.rglob("*")):
            if item.is_file() and item.name != "run.json":
                result.append({
                    "name": item.relative_to(path).as_posix(),
                    "bytes": item.stat().st_size,
                    "sha256": _sha256_file(item),
                })
        return result

    def update(self, run_id: str, *, stage: str | None = None, status: str | None = None,
               error: str | None = None, human_adjudication: str | None = None) -> dict[str, Any]:
        path, record = self.get(run_id)
        if stage is not None:
            record.setdefault("stages", {})[stage] = status or "complete"
        if error is not None:
            record["last_error"] = error
        if human_adjudication is not None:
            record["human_adjudication"] = human_adjudication
        self.write_record(path, record)
        return record
