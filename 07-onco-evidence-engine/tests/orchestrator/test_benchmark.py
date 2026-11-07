"""WP-M4.4 — benchmark / validation mode (pre-registration, sealing, post-gate reveal)."""
import json

import pytest

from core.sr.orchestrator import benchmark as bm
from core.sr.orchestrator.benchmark import BenchmarkError


def _run_dir(tmp_path):
    rd = tmp_path / "run"
    rd.mkdir()
    return rd


def _freeze(run_dir, h="deadbeef"):
    (run_dir / "protocol_freeze.json").write_text(json.dumps({"freeze_hash": h, "slug": "x"}))
    return h


def _gold(tmp_path):
    # gold lives OUTSIDE the run dir (sealed)
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    p = sealed / "gold.json"
    p.write_text(json.dumps({"included_pmids": ["1", "2", "3"], "pooled_estimate": 0.50,
                             "ci_low": 0.40, "ci_high": 0.62}))
    return p


def test_refuses_without_frozen_protocol(tmp_path):
    rd = _run_dir(tmp_path)
    with pytest.raises(BenchmarkError, match="frozen pre-registered protocol"):
        bm.start_benchmark(rd, protocol_hash="deadbeef", gold_path=_gold(tmp_path))


def test_protocol_hash_must_match_freeze(tmp_path):
    rd = _run_dir(tmp_path)
    _freeze(rd, "AAAA")
    with pytest.raises(BenchmarkError, match="freeze_hash"):
        bm.start_benchmark(rd, protocol_hash="BBBB", gold_path=_gold(tmp_path))


def test_refuses_after_search_already_ran(tmp_path):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    (rd / "corpus.json").write_text("[]")          # search already happened -> post-hoc
    with pytest.raises(BenchmarkError, match="(?i)before"):
        bm.start_benchmark(rd, protocol_hash=h, gold_path=_gold(tmp_path))


def test_refuses_when_any_stage_already_executed(tmp_path):
    from core.sr.orchestrator import state as st
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    gold = _gold(tmp_path)
    for status in ("awaiting_approval", "failed", "done"):
        s = st.RunState(run_id="r", created_utc="2026-06-16T00:00:00Z")
        s.stages["screen"] = st.StageState(name="screen", status=status)
        st.save(rd, s)
        with pytest.raises(BenchmarkError, match="already executed"):
            bm.start_benchmark(rd, protocol_hash=h, gold_path=gold)


def test_refuses_on_corrupt_run_state(tmp_path):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    (rd / "run_state.json").write_text('{"checksum": "x", "state": {"run_id": "r"}}')  # bad checksum
    with pytest.raises(BenchmarkError, match="corrupt"):
        bm.start_benchmark(rd, protocol_hash=h, gold_path=_gold(tmp_path))


def test_gold_inside_run_dir_rejected(tmp_path):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    inside = rd / "gold.json"                       # NOT sealed (inside run dir)
    inside.write_text(json.dumps({"included_pmids": ["1"]}))
    with pytest.raises(BenchmarkError, match="OUTSIDE the run directory"):
        bm.start_benchmark(rd, protocol_hash=h, gold_path=inside)


def test_in_run_symlink_to_external_gold_rejected(tmp_path):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    external = _gold(tmp_path)                      # real gold outside run dir
    link = rd / "gold.json"                         # symlink INSIDE the run dir
    link.symlink_to(external)
    with pytest.raises(BenchmarkError, match="OUTSIDE the run directory"):
        bm.start_benchmark(rd, protocol_hash=h, gold_path=link)


def test_seals_gold_commitment_only(tmp_path):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    man = bm.start_benchmark(rd, protocol_hash=h, gold_path=_gold(tmp_path))
    assert man["gold_sealed"] is True and len(man["gold_hash"]) == 64
    saved = json.loads((rd / "benchmark.json").read_text())
    assert "included_pmids" not in saved and "pooled_estimate" not in saved
    assert bm.gold_readable_from_run_dir(rd) is False
    assert not (rd / "gold.json").exists()         # no gold artifact inside the run dir


def test_compare_fails_closed_when_gates_not_cleared(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    gold = _gold(tmp_path)
    bm.start_benchmark(rd, protocol_hash=h, gold_path=gold)
    monkeypatch.setattr(bm, "_gates_cleared", lambda run_dir: False)
    with pytest.raises(BenchmarkError, match="gates not cleared"):
        bm.benchmark_compare(rd, gold, our_included=["1"])


def test_compare_detects_gold_tamper(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    gold = _gold(tmp_path)
    bm.start_benchmark(rd, protocol_hash=h, gold_path=gold)
    monkeypatch.setattr(bm, "_gates_cleared", lambda run_dir: True)
    gold.write_text(json.dumps({"included_pmids": ["9"], "pooled_estimate": 0.99}))  # swap after seal
    with pytest.raises(BenchmarkError, match="tampered|swapped|commitment"):
        bm.benchmark_compare(rd, gold, our_included=["1", "2"])


def test_compare_computes_prespecified_metrics(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path)
    h = _freeze(rd)
    gold = _gold(tmp_path)
    bm.start_benchmark(rd, protocol_hash=h, gold_path=gold)
    monkeypatch.setattr(bm, "_gates_cleared", lambda run_dir: True)
    res = bm.benchmark_compare(rd, gold, our_included=["1", "2", "4"],
                               our_estimate=0.48, our_ci=(0.39, 0.60))
    assert res["included"]["tp"] == 2 and res["included"]["fp"] == 1 and res["included"]["fn"] == 1
    assert 0.66 <= res["included"]["f1"] <= 0.67
    assert res["estimate"]["published_point_in_our_ci"] is True      # 0.50 in [0.39,0.60]
    assert res["estimate"]["ci_overlap"] is True
    assert res["estimate"]["ratio"] == round(0.48 / 0.50, 4)
