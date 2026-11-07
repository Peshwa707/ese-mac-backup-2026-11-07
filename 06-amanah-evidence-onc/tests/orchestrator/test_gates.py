"""WP-M1.5 — machine-enforced honesty gates (§7a)."""
import pytest

from core.sr.orchestrator import gates as g
from core.sr.orchestrator.stages import Stage
from core.sr.orchestrator.state import RunState, StageState

KEY = b"x" * 32
TS = "2026-06-16T00:00:00Z"


def _noop(run_dir, ctx):
    return None


def _artifact(tmp_path, name="extractions.json", body='{"a": 1}'):
    (tmp_path / name).write_text(body, encoding="utf-8")
    return name


# ---- signing ----
def test_signature_roundtrip(tmp_path):
    name = _artifact(tmp_path)
    rec = g.approve(tmp_path, "extract", "dr.smith", name, ts_utc=TS, key=KEY)
    assert g.verify_record(rec, tmp_path / name, KEY) is True
    assert g.verify_record(rec, tmp_path / name, b"y" * 32) is False   # wrong key


def test_verify_fails_on_mutated_artifact(tmp_path):
    name = _artifact(tmp_path)
    rec = g.approve(tmp_path, "extract", "dr.smith", name, ts_utc=TS, key=KEY)
    (tmp_path / name).write_text('{"a": 2}', encoding="utf-8")          # tamper post-approval
    assert g.verify_record(rec, tmp_path / name, KEY) is False


def test_approve_refuses_non_human_actor(tmp_path):
    name = _artifact(tmp_path)
    for actor in ("auto", "", "AI", "orchestrator"):
        with pytest.raises(ValueError, match="human actor"):
            g.approve(tmp_path, "extract", actor, name, ts_utc=TS, key=KEY)


def test_load_or_create_key_persists(tmp_path):
    p = tmp_path / "k" / "gate.key"
    k1 = g.load_or_create_key(p)
    assert p.exists() and len(k1) == 32
    assert g.load_or_create_key(p) == k1                               # stable across loads


# ---- export gating ----
def _stages():
    return [
        Stage("search", _noop),
        Stage("extract", _noop, requires=("search",), honesty_gate=True,
              gate_artifact="extractions.json"),
        Stage("manuscript", _noop, requires=("extract",), honesty_gate=True,
              gate_artifact="manuscript.json"),
    ]


def _state(**statuses):
    st = RunState(run_id="r", created_utc=TS)
    for n, s in statuses.items():
        st.stages[n] = StageState(name=n, status=s)
    return st


def test_export_blocked_without_gate_record(tmp_path):
    st = _state(search="done", extract="done", manuscript="done")
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is False
    assert any("no signed human approval" in r for r in reasons)


def test_hand_edited_approved_flag_blocks_export(tmp_path):
    # status flipped to approved in state, but NO signed GateRecord exists -> blocked
    st = _state(search="done", extract="approved", manuscript="approved")
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is False
    assert any("extract" in r for r in reasons)


def test_auto_run_cannot_reach_export(tmp_path):
    # --auto never calls approve(); without records export stays blocked
    st = _state(search="done", extract="done", manuscript="done")
    assert g.export_allowed(tmp_path, st, _stages(), key=KEY)[0] is False


def test_export_allowed_when_gates_valid_and_complete(tmp_path):
    ext = _artifact(tmp_path, "extractions.json")
    man = _artifact(tmp_path, "manuscript.json", '{"m": 1}')
    g.approve(tmp_path, "extract", "dr.smith", ext, ts_utc=TS, key=KEY)
    g.approve(tmp_path, "manuscript", "dr.smith", man, ts_utc=TS, key=KEY)
    st = _state(search="done", extract="approved", manuscript="approved")
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is True, reasons


def test_export_reverifies_against_current_bytes(tmp_path):
    ext = _artifact(tmp_path, "extractions.json")
    man = _artifact(tmp_path, "manuscript.json", '{"m": 1}')
    g.approve(tmp_path, "extract", "dr.smith", ext, ts_utc=TS, key=KEY)
    g.approve(tmp_path, "manuscript", "dr.smith", man, ts_utc=TS, key=KEY)
    st = _state(search="done", extract="approved", manuscript="approved")
    assert g.export_allowed(tmp_path, st, _stages(), key=KEY)[0] is True
    (tmp_path / "extractions.json").write_text('{"a": 999}', encoding="utf-8")  # edit after approval
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is False
    assert any("does not match current artifact" in r for r in reasons)


def test_copied_gate_record_rejected(tmp_path):
    # a valid 'extract' approval copied to gates/manuscript.json must NOT clear manuscript
    ext = _artifact(tmp_path, "extractions.json")
    _artifact(tmp_path, "manuscript.json", '{"m": 1}')
    g.approve(tmp_path, "extract", "dr.smith", ext, ts_utc=TS, key=KEY)
    import shutil
    shutil.copy(tmp_path / "gates" / "extract.json", tmp_path / "gates" / "manuscript.json")
    st = _state(search="done", extract="approved", manuscript="approved")
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is False
    assert any("manuscript" in r for r in reasons)


def test_wrong_artifact_record_rejected(tmp_path):
    # an approval for manuscript bound to the wrong artifact name must not clear
    _artifact(tmp_path, "extractions.json")
    _artifact(tmp_path, "manuscript.json", '{"m": 1}')
    g.approve(tmp_path, "extract", "dr.smith", "extractions.json", ts_utc=TS, key=KEY)
    # forge a manuscript record bound to extractions.json (not the expected manuscript.json)
    bad = g.sign_approval("manuscript", "dr.smith", TS, "extractions.json",
                          g.hash_file(tmp_path / "extractions.json"), KEY)
    (tmp_path / "gates").mkdir(exist_ok=True)
    (tmp_path / "gates" / "manuscript.json").write_text(bad.model_dump_json(), encoding="utf-8")
    st = _state(search="done", extract="approved", manuscript="approved")
    assert g.export_allowed(tmp_path, st, _stages(), key=KEY)[0] is False


def test_signed_non_human_record_rejected(tmp_path):
    # even a correctly-signed record with a non-human actor must not clear (export path)
    _artifact(tmp_path, "extractions.json")
    man = _artifact(tmp_path, "manuscript.json", '{"m": 1}')
    g.approve(tmp_path, "manuscript", "dr.smith", man, ts_utc=TS, key=KEY)
    bad = g.sign_approval("extract", "auto", TS, "extractions.json",
                          g.hash_file(tmp_path / "extractions.json"), KEY)
    (tmp_path / "gates").mkdir(exist_ok=True)
    (tmp_path / "gates" / "extract.json").write_text(bad.model_dump_json(), encoding="utf-8")
    st = _state(search="done", extract="approved", manuscript="approved")
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is False
    assert any("extract" in r for r in reasons)


def test_incomplete_stage_blocks_export(tmp_path):
    ext = _artifact(tmp_path, "extractions.json")
    man = _artifact(tmp_path, "manuscript.json", '{"m": 1}')
    g.approve(tmp_path, "extract", "dr.smith", ext, ts_utc=TS, key=KEY)
    g.approve(tmp_path, "manuscript", "dr.smith", man, ts_utc=TS, key=KEY)
    st = _state(search="stale", extract="approved", manuscript="approved")  # upstream stale
    ok, reasons = g.export_allowed(tmp_path, st, _stages(), key=KEY)
    assert ok is False
    assert any("search" in r and "stale" in r for r in reasons)
