"""Content hashing + staleness (WP-M1.2).

Every stage records the sha256 of the artifacts it consumed (``input_hashes``) and
produced (``output_hashes``). A stage is *stale* when any artifact it consumed no
longer matches the current hash of the upstream stage that produced it — so an edit
upstream is detected by content, never by a trusted flag (spec §5).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.sr.orchestrator.state import StageState

_CHUNK = 1 << 20  # 1 MiB streaming reads for large artifacts


def hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_str_keys(obj) -> None:
    """Reject non-string object keys so canonicalization is unambiguous (int/str key
    mixes can coerce or raise mid-sort, producing wrong or non-deterministic hashes)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"hash_json requires string object keys; got {type(k).__name__}")
            _require_str_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _require_str_keys(v)


def hash_json(obj) -> str:
    """Canonical (key-order-independent) hash of a JSON object with string keys. Rejects
    non-finite floats (allow_nan=False) so a hash never depends on non-standard NaN/Inf
    tokens that only Python's lenient parser would accept."""
    _require_str_keys(obj)
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return hash_bytes(canonical.encode("utf-8"))


def is_stale(stage: StageState, upstream: dict[str, str]) -> bool:
    """True if any artifact the stage consumed differs from the current upstream hash.

    ``upstream`` maps artifact name -> its current producing-stage output hash. A
    consumed artifact that is now absent upstream counts as changed (stale).
    """
    for artifact, recorded in stage.input_hashes.items():
        if upstream.get(artifact) != recorded:
            return True
    return False
