"""WP-M1.3 — stage model, dependency DAG, scheduler."""
import pytest

from core.sr.orchestrator import stages as sg
from core.sr.orchestrator.stages import Stage
from core.sr.orchestrator.state import RunState, StageState


def _noop(run_dir, ctx):  # uniform adapter signature placeholder
    return None


def _registry():
    # search -> screen -> {extract -> {stats -> manuscript}}; rob -> grade -> manuscript
    return [
        Stage("search", _noop, produces=("corpus.json",)),
        Stage("screen", _noop, requires=("search",)),
        Stage("extract", _noop, requires=("screen",)),
        Stage("rob", _noop, requires=("extract",)),
        Stage("grade", _noop, requires=("rob",)),
        Stage("stats", _noop, requires=("extract",)),
        Stage("manuscript", _noop, requires=("stats", "grade"), honesty_gate=True),
    ]


def _state(profile="systematic", **statuses):
    st = RunState(run_id="r", profile=profile, created_utc="2026-06-16T00:00:00Z")
    for name, status in statuses.items():
        st.stages[name] = StageState(name=name, status=status)
    return st


def test_topo_order_respects_requires():
    order = [s.name for s in sg.topo_order(_registry(), "systematic")]
    assert order.index("search") < order.index("screen") < order.index("extract")
    assert order.index("extract") < order.index("stats") < order.index("manuscript")
    assert order.index("rob") < order.index("grade") < order.index("manuscript")


def test_topo_detects_cycle():
    cyclic = [Stage("a", _noop, requires=("b",)), Stage("b", _noop, requires=("a",))]
    with pytest.raises(ValueError, match="cycle"):
        sg.topo_order(cyclic, "systematic")


def test_topo_rejects_unknown_dependency():
    bad = [Stage("a", _noop, requires=("ghost",))]
    with pytest.raises(ValueError, match="unknown stage"):
        sg.topo_order(bad, "systematic")


def test_topo_rejects_unknown_profile():
    with pytest.raises(ValueError, match="unknown profile"):
        sg.topo_order(_registry(), "bogus")


def test_next_stage_fails_closed_on_unknown_profile():
    # a corrupted RunState.profile must raise, not look "complete"
    st = _state(profile="bogus", search="done")
    with pytest.raises(ValueError, match="unknown profile"):
        sg.next_stage(st, _registry())


def test_next_stage_returns_first_runnable():
    st = _state(search="done")
    assert sg.next_stage(st, _registry()).name == "screen"


def test_next_stage_skips_done_and_blocks_on_unsatisfied_dep():
    st = _state(search="done", screen="done", extract="done", stats="done")
    # rob still pending -> grade not runnable; rob is the next runnable
    assert sg.next_stage(st, _registry()).name == "rob"


def test_next_stage_waits_on_awaiting_approval_dep():
    # extract done; stats awaiting human approval -> manuscript NOT runnable
    st = _state(search="done", screen="done", extract="done",
                rob="done", grade="done", stats="awaiting_approval")
    nxt = sg.next_stage(st, _registry())
    assert nxt is None or nxt.name != "manuscript"


def test_next_stage_none_when_complete():
    st = _state(search="done", screen="done", extract="done", rob="done",
                grade="done", stats="done", manuscript="approved")
    assert sg.next_stage(st, _registry()) is None


def test_restale_only_true_descendants():
    # editing rob restales grade + manuscript, but NOT stats (stats requires extract, not rob)
    out = sg.restale(_registry(), "rob")
    assert out == {"grade", "manuscript"}
    assert "stats" not in out


def test_restale_from_root_hits_everything_downstream():
    out = sg.restale(_registry(), "search")
    assert {"screen", "extract", "stats", "rob", "grade", "manuscript"} <= out


def test_profile_filter_excludes_stages():
    reg = [
        Stage("search", _noop),
        Stage("stats", _noop, requires=("search",), profiles=frozenset({"systematic"})),
    ]
    names = [s.name for s in sg.topo_order(reg, "scoping")]
    assert names == ["search"]                      # stats excluded from scoping
