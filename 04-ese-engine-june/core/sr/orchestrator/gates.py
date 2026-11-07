"""Machine-enforced honesty gates (WP-M1.5, spec §7a).

A 🔒 honesty gate (screening adjudication, extraction sign-off, manuscript export)
clears ONLY with a signed record that binds a human actor + timestamp to the SHA-256
of the exact artifact bytes approved. Export independently re-verifies every record
against the *current* bytes — a hand-edited status flag, an un-signed approval, or a
post-approval artifact change all fail closed. ``--auto`` can never produce a valid
record (the actor must be a real human), so an automated run cannot reach export.

Signing uses HMAC-SHA256 over the canonical fields with a local machine key. This is
tamper-evidence against in-tree edits, not protection against a determined attacker
who also holds the key; that trade-off is acceptable for a single-user research tool
(noted in the spec's M2 open questions for a keychain upgrade).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from pydantic import BaseModel

from core.sr.orchestrator.hashing import hash_file

# Actors that are NOT a human — a 🔒 gate must never clear under these.
_NON_HUMAN_ACTORS = {"", "auto", "system", "orchestrator", "ai", "copilot"}


class GateRecord(BaseModel):
    gate: str
    actor: str
    ts_utc: str
    artifact: str            # artifact name relative to run_dir the approval is bound to
    artifact_sha256: str
    signature: str           # HMAC-SHA256 over the canonical message


def _key_path() -> Path:
    env = os.environ.get("ESE_GATE_KEY") or os.environ.get("ONCO_GATE_KEY")
    if env:
        return Path(env)
    # Study-local default (gitignored), CWD-relative like runs/ — so an installed wheel
    # writes the key into the active study dir, never under site-packages. ESE_GATE_KEY
    # overrides for an explicit location.
    return Path.cwd() / ".ese" / "gate.key"


def load_or_create_key(path: Path | None = None) -> bytes:
    """Load the local signing key, creating a 0600 random key on first use."""
    path = Path(path) if path else _key_path()
    if path.exists():
        if path.is_symlink():
            raise RuntimeError(f"{path} is a symlink (refused)")
        info = path.stat()
        if info.st_mode & 0o077:
            raise RuntimeError(f"{path} permissions too open (must be 0600)")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RuntimeError(f"{path} not owned by the current user (refused)")
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = os.urandom(32)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # may fail on exotic filesystems; verified below
    # Verify the key is owner-only — a world/group-readable signing key lets anyone forge
    # gate approvals, so fail closed rather than ship a weak key silently.
    if hasattr(os, "getuid"):
        mode = path.stat().st_mode
        if mode & 0o077:
            path.unlink(missing_ok=True)
            raise RuntimeError(f"could not create an owner-only gate key at {path} "
                               f"(mode {oct(mode & 0o777)}); refusing to use a weak key")
    return key


def _message(gate: str, actor: str, ts_utc: str, artifact: str, artifact_sha256: str) -> bytes:
    return "|".join([gate, actor, ts_utc, artifact, artifact_sha256]).encode("utf-8")


def sign_approval(gate: str, actor: str, ts_utc: str, artifact: str,
                  artifact_sha256: str, key: bytes) -> GateRecord:
    sig = hmac.new(key, _message(gate, actor, ts_utc, artifact, artifact_sha256),
                   hashlib.sha256).hexdigest()
    return GateRecord(gate=gate, actor=actor, ts_utc=ts_utc, artifact=artifact,
                      artifact_sha256=artifact_sha256, signature=sig)


def verify_record(rec: GateRecord, artifact_path: Path, key: bytes, *,
                  expected_gate: str | None = None, expected_artifact: str | None = None) -> bool:
    """True iff the record is a valid human approval bound to the *expected* gate and
    artifact, and the artifact's current bytes match.

    The export path passes ``expected_gate``/``expected_artifact`` so a record cannot be
    copied to another gate or bound to an unrelated artifact, and re-checks the
    human-actor rule (export trusts stored records, so it must not rely on approve()
    alone)."""
    if rec.actor.strip().lower() in _NON_HUMAN_ACTORS:
        return False                                          # signed non-human record
    if expected_gate is not None and rec.gate != expected_gate:
        return False                                          # record copied across gates
    if expected_artifact is not None and rec.artifact != expected_artifact:
        return False                                          # bound to the wrong artifact
    artifact_path = Path(artifact_path)
    if not artifact_path.exists():
        return False
    if not hmac.compare_digest(hash_file(artifact_path), rec.artifact_sha256):
        return False
    expected = hmac.new(
        key, _message(rec.gate, rec.actor, rec.ts_utc, rec.artifact, rec.artifact_sha256),
        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, rec.signature)


def _gate_file(run_dir: Path, gate: str) -> Path:
    return Path(run_dir) / "gates" / f"{gate}.json"


def load_record(run_dir: Path, gate: str) -> GateRecord | None:
    p = _gate_file(run_dir, gate)
    if not p.exists():
        return None
    return GateRecord.model_validate_json(p.read_text(encoding="utf-8"))


def approve(run_dir: Path, gate: str, actor: str, artifact: str, *,
            ts_utc: str, key: bytes | None = None) -> GateRecord:
    """Record a human approval bound to the current bytes of ``artifact``.

    Refuses a non-human actor (so ``--auto`` cannot self-clear a 🔒 gate)."""
    if actor.strip().lower() in _NON_HUMAN_ACTORS:
        raise ValueError(f"honesty gate {gate!r} requires a human actor; got {actor!r}")
    art_path = Path(run_dir) / artifact
    if not art_path.exists():
        raise FileNotFoundError(art_path)
    key = key if key is not None else load_or_create_key()
    rec = sign_approval(gate, actor, ts_utc, artifact, hash_file(art_path), key)
    gate_file = _gate_file(run_dir, gate)
    gate_file.parent.mkdir(parents=True, exist_ok=True)
    gate_file.write_text(rec.model_dump_json(indent=2), encoding="utf-8")
    return rec


def export_allowed(run_dir: Path, state, stages, *, key: bytes | None = None) -> tuple[bool, list[str]]:
    """Independent pre-export check. Allowed only when every honesty-gate stage has a
    valid signed record matching current bytes AND every stage is done/approved."""
    key = key if key is not None else load_or_create_key()
    reasons: list[str] = []
    for s in stages:
        ss = state.stages.get(s.name)
        status = ss.status if ss else "pending"
        if getattr(s, "honesty_gate", False):
            expected_artifact = getattr(s, "gate_artifact", "") or ""
            rec = load_record(run_dir, s.name)
            if not expected_artifact:
                reasons.append(f"{s.name}: honesty gate declares no gate_artifact")
            elif rec is None:
                reasons.append(f"{s.name}: no signed human approval")
            # Verify against the EXPECTED artifact path (not the record's self-declared
            # one) so a copied/wrong-artifact/non-human record cannot clear the gate.
            elif not verify_record(rec, Path(run_dir) / expected_artifact, key,
                                   expected_gate=s.name, expected_artifact=expected_artifact):
                reasons.append(f"{s.name}: approval does not match current artifact bytes")
        if status not in ("done", "approved"):
            reasons.append(f"{s.name}: status {status!r} (not done/approved)")
    return (not reasons, reasons)
