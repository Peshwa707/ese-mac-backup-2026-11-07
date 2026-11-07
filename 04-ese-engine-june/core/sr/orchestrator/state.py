"""Atomic, lock-guarded, integrity-checked run-state store (WP-M1.1).

The single source of truth for a run lives at ``runs/<id>/run_state.json``. It is
written atomically (tmp + fsync + os.replace) so a crash mid-write can never brick
``resume``; it carries a content checksum so corruption is detected on load; and it
is guarded by an exclusive ``flock`` lease so two processes/resumes can't race.

No wall-clock is read here (``created_utc`` is supplied by the caller) so the module
is deterministic and testable.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
_STATE_FILE = "run_state.json"
_LOCK_FILE = ".run.lock"

StageStatus = Literal[
    "pending", "running", "awaiting_approval", "approved", "done", "failed", "stale"
]


class StateCorruption(RuntimeError):
    """run_state.json is malformed, truncated, or fails its checksum."""


class RunLocked(RuntimeError):
    """Another holder owns the run lock."""


class StageState(BaseModel):
    name: str
    status: StageStatus = "pending"
    input_hashes: dict[str, str] = Field(default_factory=dict)   # artifact -> sha256 consumed
    output_hashes: dict[str, str] = Field(default_factory=dict)  # artifact -> sha256 produced
    params_hash: str = ""                                        # config/code/prompt fingerprint
    error_code: str = ""                                         # scrubbed; never raw exception text
    gate_record_id: str = ""                                     # set when an honesty gate clears


class RunState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    profile: str = "systematic"
    auto: bool = False
    stages: dict[str, StageState] = Field(default_factory=dict)
    created_utc: str = ""


def _checksum_obj(obj: dict) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def migrate(inner: dict) -> dict:
    """Bring an older stored state dict forward to the current schema. Idempotent.

    A version newer than this build is rejected (a newer format must NOT be
    silently misread as the current schema)."""
    inner = dict(inner)
    v = inner.get("schema_version", 0)
    if isinstance(v, bool) or not isinstance(v, int):
        raise StateCorruption(f"invalid schema_version {v!r}")
    if v > SCHEMA_VERSION:
        raise StateCorruption(
            f"run state schema_version {v} is newer than supported {SCHEMA_VERSION}")
    # Future per-version migrations (for 0 < v < SCHEMA_VERSION) go here.
    inner["schema_version"] = SCHEMA_VERSION
    return inner


def _fsync_dir(d: Path) -> None:
    """Best-effort fsync of a directory so a rename is crash-durable (POSIX)."""
    flags = getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(d, flags)
    except OSError:
        return  # platform without directory fds (e.g. Windows)
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save(run_dir: Path, state: RunState) -> None:
    """Atomically persist ``state`` to ``run_dir/run_state.json`` with a checksum.

    The checksum is computed over the serialized inner state; if ``os.replace``
    fails, the previously-committed file is left untouched.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    inner = state.model_dump(mode="json")
    payload = {"checksum": _checksum_obj(inner), "state": inner}
    data = json.dumps(payload, indent=2).encode("utf-8")
    tmp = run_dir / (_STATE_FILE + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, run_dir / _STATE_FILE)
    except Exception:
        # Leave the committed file intact; drop the partial tmp.
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(run_dir)   # make the rename itself crash-durable


def load(run_dir: Path) -> RunState:
    """Load + validate the run state; raise ``StateCorruption`` on any integrity failure."""
    path = Path(run_dir) / _STATE_FILE
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise StateCorruption(f"unparseable run state at {path}: {e}") from e
    if not isinstance(payload, dict) or "state" not in payload or "checksum" not in payload:
        raise StateCorruption(f"malformed run state envelope at {path}")
    inner = payload["state"]
    if not isinstance(inner, dict) or _checksum_obj(inner) != payload["checksum"]:
        raise StateCorruption(f"checksum mismatch at {path}")
    try:
        return RunState.model_validate(migrate(inner))
    except Exception as e:  # pydantic ValidationError, etc.
        raise StateCorruption(f"invalid run state at {path}: {e}") from e


@contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    """Exclusive, non-blocking lease over a run dir. Raises ``RunLocked`` if held.

    The lock is per open-file-description, so a second acquisition (even in the same
    process via a separate open) conflicts — preventing concurrent resumes.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    f = open(run_dir / _LOCK_FILE, "w")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise RunLocked(f"run dir already locked: {run_dir}") from e
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()
