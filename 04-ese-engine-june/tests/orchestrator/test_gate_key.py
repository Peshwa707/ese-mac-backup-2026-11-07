"""Gate-key path + existing-key safety checks."""

from __future__ import annotations

import pytest

from core.sr.orchestrator import gates as g


def test_key_path_defaults_to_cwd_local_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ESE_GATE_KEY", raising=False)
    monkeypatch.delenv("ONCO_GATE_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    assert g._key_path() == tmp_path / ".ese" / "gate.key"


def test_key_path_honors_explicit_env(tmp_path, monkeypatch):
    explicit = tmp_path / "keys" / "gate.key"
    monkeypatch.setenv("ESE_GATE_KEY", str(explicit))
    monkeypatch.delenv("ONCO_GATE_KEY", raising=False)

    assert g._key_path() == explicit


def test_load_or_create_key_reads_existing_secure_key(tmp_path):
    key_path = tmp_path / "gate.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)

    assert g.load_or_create_key(key_path) == b"k" * 32


def test_load_or_create_key_rejects_existing_symlink(tmp_path):
    real = tmp_path / "real.key"
    real.write_bytes(b"k" * 32)
    real.chmod(0o600)
    link = tmp_path / "gate.key"
    link.symlink_to(real)

    with pytest.raises(RuntimeError, match="symlink"):
        g.load_or_create_key(link)


def test_load_or_create_key_rejects_group_or_other_accessible_file(tmp_path):
    key_path = tmp_path / "gate.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o644)

    with pytest.raises(RuntimeError, match="permissions too open"):
        g.load_or_create_key(key_path)


def test_load_or_create_key_rejects_wrong_owner(tmp_path, monkeypatch):
    if not hasattr(g.os, "getuid"):
        pytest.skip("owner checks unavailable on this platform")

    key_path = tmp_path / "gate.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    if key_path.stat().st_uid == 999999:
        pytest.skip("cannot simulate a different owner uid on this platform")
    monkeypatch.setattr(g.os, "getuid", lambda: 999999, raising=False)

    with pytest.raises(RuntimeError, match="owned by"):
        g.load_or_create_key(key_path)
