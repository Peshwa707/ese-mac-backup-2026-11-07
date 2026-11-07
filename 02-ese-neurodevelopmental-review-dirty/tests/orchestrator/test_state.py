"""WP-M1.1 — atomic, lock-guarded, integrity-checked run-state store."""
import json

import pytest

from core.sr.orchestrator import state as st
from core.sr.orchestrator.state import RunState, StageState


def _run(run_id="r1", **kw):
    return RunState(run_id=run_id, created_utc="2026-06-16T00:00:00Z", **kw)


def test_save_load_roundtrip(tmp_path):
    s = _run(stages={"search": StageState(name="search", status="done",
                                          output_hashes={"corpus.json": "abc"})})
    st.save(tmp_path, s)
    loaded = st.load(tmp_path)
    assert loaded.run_id == "r1"
    assert loaded.stages["search"].status == "done"
    assert loaded.stages["search"].output_hashes == {"corpus.json": "abc"}


def test_atomic_save_survives_partial_write(tmp_path, monkeypatch):
    st.save(tmp_path, _run(profile="systematic"))
    # A later save whose os.replace fails must leave the committed file intact.
    monkeypatch.setattr(st.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        st.save(tmp_path, _run(profile="scoping"))
    assert st.load(tmp_path).profile == "systematic"   # original survived


def test_checksum_detects_corruption(tmp_path):
    st.save(tmp_path, _run())
    p = tmp_path / "run_state.json"
    payload = json.loads(p.read_text())
    payload["state"]["run_id"] = "tampered"            # change content, keep old checksum
    p.write_text(json.dumps(payload))
    with pytest.raises(st.StateCorruption):
        st.load(tmp_path)


def test_load_raises_on_truncation(tmp_path):
    st.save(tmp_path, _run())
    (tmp_path / "run_state.json").write_text('{"checksum": "x", "stat')  # truncated JSON
    with pytest.raises(st.StateCorruption):
        st.load(tmp_path)


def test_load_raises_on_raw_byte_corruption(tmp_path):
    st.save(tmp_path, _run())
    p = tmp_path / "run_state.json"
    raw = bytearray(p.read_bytes())
    raw[len(raw) // 2] = 0xFF                # inject an invalid UTF-8 byte
    p.write_bytes(bytes(raw))
    with pytest.raises(st.StateCorruption):
        st.load(tmp_path)


def test_migrate_rejects_future_version():
    with pytest.raises(st.StateCorruption):
        st.migrate({"run_id": "x", "schema_version": st.SCHEMA_VERSION + 1})


def test_run_lock_excludes_second_holder(tmp_path):
    with st.run_lock(tmp_path):
        with pytest.raises(st.RunLocked):
            with st.run_lock(tmp_path):
                pass
    # released after the block: re-acquire succeeds
    with st.run_lock(tmp_path):
        pass


def test_migrate_v0_to_current_via_load(tmp_path):
    inner = {"run_id": "r0", "profile": "systematic", "auto": False,
             "stages": {}, "created_utc": "2026-06-16T00:00:00Z"}  # no schema_version
    payload = {"checksum": st._checksum_obj(inner), "state": inner}
    (tmp_path / "run_state.json").write_text(json.dumps(payload))
    loaded = st.load(tmp_path)
    assert loaded.schema_version == st.SCHEMA_VERSION


def test_migrate_is_idempotent():
    once = st.migrate({"run_id": "x"})
    twice = st.migrate(once)
    assert once == twice and once["schema_version"] == st.SCHEMA_VERSION
