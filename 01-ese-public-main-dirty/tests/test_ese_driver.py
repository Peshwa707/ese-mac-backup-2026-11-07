"""ese driver planning and status display."""

from __future__ import annotations

from pathlib import Path

from core.sr.driver.plan import planned_next_stage, reset_earliest_failed
from core.sr.driver.status import run_status
from core.sr.orchestrator import engine
from core.sr.orchestrator.stages import topo_order
from core.sr.orchestrator import state as st


def _init_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "idea.txt").write_text("test idea\n", encoding="utf-8")
    engine.create_run(
        __import__("core.sr.config", fromlist=["load_sr_config"]).load_sr_config(),
        created_utc="2026-06-17T00:00:00Z",
        runs_dir=tmp_path,
        run_id="run1",
    )
    return run_dir


def test_status_shows_retry_for_failed_stage_before_siblings(tmp_path):
    run_dir = _init_run(tmp_path)
    state = st.load(run_dir)
    state.stages["idea_to_pico"] = st.StageState(name="idea_to_pico", status="done")
    state.stages["novelty_check"] = st.StageState(
        name="novelty_check", status="failed", error_code="MissingKeyError"
    )
    st.save(run_dir, state)

    status = run_status(run_dir)
    assert status["current"] == "novelty_check"
    assert status["current_mode"] == "retry"
    blocked = {b["name"]: b for b in status["blocked"]}
    assert blocked["novelty_check"]["error_code"] == "MissingKeyError"
    assert "ANTHROPIC_API_KEY" in blocked["novelty_check"]["hint"]


def test_frontend_dag_serializes_after_novelty(tmp_path):
    run_dir = _init_run(tmp_path)
    cfg = __import__("core.sr.config", fromlist=["load_sr_config"]).load_sr_config()
    order = topo_order(engine.build_registry(cfg, frontend=True), "systematic")
    names = [s.name for s in order]
    assert names.index("novelty_check") < names.index("feasibility_scope")
    assert names.index("feasibility_scope") < names.index("protocol_critic")
    assert names.index("protocol_critic") < names.index("autoconfig")


def test_reset_earliest_failed_makes_stage_runnable(tmp_path):
    run_dir = _init_run(tmp_path)
    state = st.load(run_dir)
    state.stages["idea_to_pico"] = st.StageState(name="idea_to_pico", status="done")
    state.stages["novelty_check"] = st.StageState(
        name="novelty_check", status="failed", error_code="MissingKeyError"
    )
    st.save(run_dir, state)
    cfg = __import__("core.sr.config", fromlist=["load_sr_config"]).load_sr_config()
    order = topo_order(engine.build_registry(cfg, frontend=True), "systematic")

    assert reset_earliest_failed(run_dir, state, order)
    state = st.load(run_dir)
    nxt, mode = planned_next_stage(state, order)
    assert nxt is not None and nxt.name == "novelty_check"
    assert state.stages["novelty_check"].status == "pending"
