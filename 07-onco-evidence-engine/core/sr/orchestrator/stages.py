"""Stage model, dependency DAG, and scheduler (WP-M1.3).

Stages form a dependency graph (``requires`` edges), NOT a linear list. Execution
order is a profile-filtered topological sort; the "next" stage is the earliest one
whose dependencies are all satisfied and which is itself pending or stale. Editing a
stage re-stales only its true transitive descendants (spec §3, §5).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from core.sr.config import SRConfig
from core.sr.orchestrator.state import RunState

_ALL_PROFILES = frozenset({"systematic", "scoping", "rapid", "living"})
_DEP_SATISFIED = {"done", "approved"}


@dataclass(frozen=True)
class Stage:
    name: str
    fn: Callable                                   # fn(run_dir: Path, ctx: RunContext) -> StageResult
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    honesty_gate: bool = False
    gate_artifact: str = ""          # the artifact a 🔒 gate's approval must bind to
    reproducibility: Literal["deterministic", "stochastic"] = "deterministic"
    profiles: frozenset[str] = _ALL_PROFILES


@dataclass
class RunContext:
    cfg: SRConfig
    run_dir: Path
    auto: bool = False
    retmax: int | None = None     # search cap (threaded from run_sr/CLI)
    connector: object | None = None
    do_extract: bool = True
    created_utc: str = ""          # run timestamp (for provenance/search-manifest)


@dataclass
class StageResult:
    outputs: dict[str, Path]                        # artifact-name -> path written
    summary: dict = field(default_factory=dict)     # human-facing, scrubbed
    deterministic_inputs: dict[str, str] = field(default_factory=dict)


def _validate(stages: list[Stage]) -> None:
    names = [s.name for s in stages]
    if len(names) != len(set(names)):
        raise ValueError("duplicate stage names in registry")
    known = set(names)
    for s in stages:
        for r in s.requires:
            if r not in known:
                raise ValueError(f"stage {s.name!r} requires unknown stage {r!r}")


def topo_order(stages: list[Stage], profile: str) -> list[Stage]:
    """Profile-filtered topological sort (stable in registry order). Raises on cycle
    or unknown-dependency. Edges to profile-excluded stages are treated as satisfied."""
    _validate(stages)
    if profile not in _ALL_PROFILES:
        # Fail closed: a mistyped/corrupted profile must not silently empty the DAG
        # and make an unrun review look complete.
        raise ValueError(f"unknown profile {profile!r}; expected one of {sorted(_ALL_PROFILES)}")
    included = [s for s in stages if profile in s.profiles]
    inc = {s.name for s in included}
    indeg = {s.name: sum(1 for r in s.requires if r in inc) for s in included}
    remaining = list(included)                      # preserves registry order
    order: list[Stage] = []
    while remaining:
        ready = [s for s in remaining if indeg[s.name] == 0]
        if not ready:
            raise ValueError("cycle detected in stage dependencies")
        nxt = ready[0]
        order.append(nxt)
        remaining.remove(nxt)
        for s in remaining:
            if nxt.name in s.requires:
                indeg[s.name] -= 1
    return order


def _status(state: RunState, name: str) -> str:
    ss = state.stages.get(name)
    return ss.status if ss else "pending"


def next_stage(state: RunState, stages: list[Stage]) -> Stage | None:
    """First stage in topo order that is pending/stale and whose deps are all
    done/approved. Returns None when nothing is runnable (complete or blocked on a gate)."""
    order = topo_order(stages, state.profile)
    in_order = {s.name for s in order}
    for s in order:
        if _status(state, s.name) not in ("pending", "stale"):
            continue
        if all(_status(state, r) in _DEP_SATISFIED for r in s.requires if r in in_order):
            return s
    return None


def restale(stages: list[Stage], edited: str) -> set[str]:
    """Transitive descendants of ``edited`` over ``requires`` edges (excludes ``edited``).

    Only true dependents are returned — editing an optional stage does not stale
    siblings that never consumed its output."""
    children: dict[str, set[str]] = {}
    for s in stages:
        for r in s.requires:
            children.setdefault(r, set()).add(s.name)
    out: set[str] = set()
    stack = list(children.get(edited, set()))
    while stack:
        n = stack.pop()
        if n not in out:
            out.add(n)
            stack.extend(children.get(n, set()))
    return out
