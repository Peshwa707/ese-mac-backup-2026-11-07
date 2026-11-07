"""Orchestrator lifecycle + stage registry (WP-M1.7).

Wires the WP-M1.4 adapters into a dependency DAG and drives them over the WP-M1.1
state store, recording content hashes per stage (WP-M1.2) and honoring the honesty
gates (WP-M1.5). The sequencing that used to live in pipeline.run_sr now lives here;
run_sr is retained as a legacy direct path (its callers/tests are unchanged) while
this engine is the canonical driver going forward.

No wall-clock is read here — callers pass ``created_utc``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from core.sr.config import SRConfig
from core.sr.orchestrator import adapters, gates
from core.sr.orchestrator import state as st
from core.sr.orchestrator.hashing import hash_file
from core.sr.orchestrator.stages import RunContext, Stage
from core.sr.orchestrator.stages import next_stage as _next_stage

from core.sr.orchestrator.registry import profiles_for_stage

RUNS_DIR = Path("runs")


class RunBlocked(RuntimeError):
    """run_to() could not bring the target to a terminal successful state (a honesty
    gate is awaiting approval, a stage failed, or an output is missing)."""


def build_registry(cfg=None, *, frontend: bool = False) -> list[Stage]:
    """The stage DAG over the pipeline. Set ``frontend=True`` to prepend M3 idea→protocol stages."""
    from core.sr.orchestrator import dual_screen, frontend as fe, protocol_critic
    screen_fn = (dual_screen.dual_screen_adapter
                 if (cfg is not None and getattr(cfg, "reviewers", None))
                 else adapters.screen_adapter)

    def _s(name, fn, *, requires=(), produces=(), honesty_gate=False, gate_artifact="",
           reproducibility="deterministic"):
        return Stage(name, fn, requires=requires, produces=produces, honesty_gate=honesty_gate,
                     gate_artifact=gate_artifact, reproducibility=reproducibility,
                     profiles=profiles_for_stage(name))

    stages: list[Stage] = []
    if frontend:
        stages += [
            _s("idea_to_pico", fe.idea_to_pico_adapter, produces=("pico.json",),
               reproducibility="stochastic"),
            _s("novelty_check", fe.novelty_check_adapter, requires=("idea_to_pico",),
               produces=("novelty_report.json",), reproducibility="stochastic"),
            _s("feasibility_scope", fe.feasibility_scope_adapter, requires=("novelty_check",),
               produces=("feasibility_report.json",), reproducibility="stochastic"),
            _s("protocol_critic", protocol_critic.protocol_critic_adapter,
               requires=("feasibility_scope",), produces=("protocol_critique.json",)),
            _s("autoconfig", fe.autoconfig_adapter, requires=("protocol_critic",),
               produces=("generated_vertical/pico.yaml",), reproducibility="stochastic"),
        ]
    stages += [
        _s("search", adapters.search_adapter, requires=("autoconfig",) if frontend else (),
           produces=("corpus.json",), reproducibility="stochastic"),
        _s("prescreen", adapters.prescreen_adapter, requires=("search",),
           produces=("prescreen.json", "to_screen.json")),
        _s("screen", screen_fn, requires=("prescreen",),
           produces=("screening.json",), honesty_gate=True, gate_artifact="screening.json",
           reproducibility="stochastic"),
        _s("fulltext", adapters.fulltext_adapter, requires=("screen",),
           produces=("fulltext_coverage.json",), reproducibility="stochastic"),
        _s("extract", adapters.extract_adapter, requires=("screen",),
           produces=("extractions.json",), honesty_gate=True, gate_artifact="extractions.json",
           reproducibility="stochastic"),
        _s("rob", adapters.rob_adapter, requires=("extract",), produces=("rob.json",)),
        _s("stats", adapters.stats_adapter, requires=("extract",), produces=("stats.json",)),
        _s("grade", adapters.grade_adapter, requires=("extract",), produces=("grade.json",)),
        _s("evidence_strength", _evidence_strength_adapter, requires=("stats",),
           produces=("evidence_strength.json",)),
        _s("assemble", adapters.assemble_adapter,
           requires=("prescreen", "screen", "extract", "evidence_strength"),
           produces=("srrun.json",)),
        # `narrative` is a REQUIRED prerequisite of `manuscript` (intentional): an
        # orchestrated run only reaches manuscript after the AI-drafted, citation-checked
        # prose is produced, so completing a run needs a cloud LLM. (The standalone
        # `ese export` CLI stays additive — it renders the prose only if present.)
        _s("narrative", adapters.narrative_adapter,
           requires=("extract", "rob", "grade", "stats"),
           produces=("narrative.json", "narrative_final.json"),
           reproducibility="stochastic"),
        _s("manuscript", adapters.manuscript_adapter, requires=("assemble", "narrative"),
           produces=("manuscript_status.json",)),
    ]
    if not frontend:
        # Drop frontend-only dependency edges when the M3 chain is not active.
        stages = [
            Stage(s.name, s.fn,
                  requires=tuple(r for r in s.requires if r != "autoconfig"),
                  produces=s.produces, honesty_gate=s.honesty_gate,
                  gate_artifact=s.gate_artifact, reproducibility=s.reproducibility,
                  profiles=s.profiles)
            for s in stages
        ]
    return stages


def _evidence_strength_adapter(run_dir, ctx):
    # imported lazily to avoid a module import cycle (evidence_strength -> protocol_critic)
    from core.sr.orchestrator.evidence_strength import evidence_strength_adapter
    return evidence_strength_adapter(run_dir, ctx)


def create_run(cfg: SRConfig, *, created_utc: str, profile: str = "systematic",
               auto: bool = False, runs_dir: Path = RUNS_DIR, run_id: str | None = None) -> Path:
    run_id = run_id or uuid.uuid4().hex
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    st.save(run_dir, st.RunState(run_id=run_id, profile=profile,
                                 vertical=getattr(cfg, "vertical", ""),
                                 auto=auto, created_utc=created_utc))
    return run_dir


def run_stage(run_dir: Path, stage: Stage, cfg: SRConfig, *, retmax: int | None = None,
              connector: object | None = None, do_extract: bool = True):
    """Execute one stage: mark running, run the adapter, record hashes, set terminal
    status. A honesty gate in guided mode lands in awaiting_approval (a human must
    approve()); in auto mode it lands in done (ran, unapproved -> export stays blocked)."""
    state = st.load(run_dir)
    ss = state.stages.setdefault(stage.name, st.StageState(name=stage.name))
    ss.status = "running"
    st.save(run_dir, state)
    try:
        ctx = RunContext(cfg=cfg, run_dir=Path(run_dir), auto=state.auto,
                         retmax=retmax, connector=connector, do_extract=do_extract,
                         created_utc=state.created_utc)
        result = stage.fn(run_dir, ctx)
    except Exception as e:
        from core.sr.orchestrator.security import scrub_exception
        state = st.load(run_dir)
        ss = state.stages[stage.name]
        ss.status = "failed"
        ss.error_code = scrub_exception(e)     # type (+http status) only — never the message/payload
        st.save(run_dir, state)
        raise
    state = st.load(run_dir)
    ss = state.stages[stage.name]
    ss.output_hashes = {name: hash_file(Path(p)) for name, p in (result.outputs or {}).items()}
    ss.input_hashes = dict(result.deterministic_inputs or {})
    ss.status = "awaiting_approval" if (stage.honesty_gate and not state.auto) else "done"
    st.save(run_dir, state)
    return result


def approve(run_dir: Path, stage_name: str, actor: str, *, ts_utc: str, key=None) -> None:
    """Record a human approval for a honesty gate and mark the stage approved."""
    stage = {s.name: s for s in build_registry()}[stage_name]
    gates.approve(run_dir, stage_name, actor, stage.gate_artifact, ts_utc=ts_utc, key=key)
    state = st.load(run_dir)
    ss = state.stages[stage_name]
    ss.status = "approved"
    ss.gate_record_id = stage_name
    st.save(run_dir, state)


def export_gate(run_dir: Path, *, cfg=None, key=None) -> tuple[bool, list[str]] | None:
    """Decide whether a run dir may export its manuscript.

    Returns ``None`` when the run was not produced by this orchestrator (no
    run_state.json AND no gate records) — such legacy/manual runs stay ungated
    for backward compatibility. Returns ``(False, reasons)`` (fail closed) when
    the state is corrupt, or when gate records exist but run_state.json is gone
    (a downgrade/tamper attempt). Otherwise returns ``gates.export_allowed(...)``:
    ``(True, [])`` when every honesty gate is signed against current bytes and
    every stage is done/approved, else ``(False, reasons)``."""
    try:
        state = st.load(run_dir)
    except FileNotFoundError:
        # No run state. Only treat as ungated legacy if no gate records exist; a
        # populated gates/ dir without run_state.json means the state was removed,
        # so refuse rather than silently downgrading to "ungated".
        gates_dir = Path(run_dir) / "gates"
        if gates_dir.is_dir() and any(gates_dir.glob("*.json")):
            return (False, ["run_state.json is missing but gate records exist "
                            "(possible tampering); refusing export"])
        return None
    except st.StateCorruption as exc:
        return (False, [f"run state is corrupt; refusing export: {exc}"])
    return gates.export_allowed(run_dir, state, build_registry(cfg), key=key)


def _refresh_staleness(run_dir: Path, state, stages: list[Stage]) -> bool:
    """Detect upstream artifact EDITS (on disk) since a stage last ran: hash each producer's
    current output file and, if a done/approved stage consumed a now-different artifact,
    mark it + its true descendants ``stale`` so they re-run. Returns True if anything changed."""
    from core.sr.orchestrator.hashing import hash_file, is_stale
    from core.sr.orchestrator.stages import restale

    current: dict[str, str] = {}
    for s in stages:
        for art in s.produces:
            p = Path(run_dir) / art
            if p.exists():
                current[art] = hash_file(p)
    changed = False
    for s in stages:
        ss = state.stages.get(s.name)
        if not ss or ss.status not in ("done", "approved"):
            continue
        if is_stale(ss, current):
            ss.status = "stale"
            changed = True
            for desc in restale(stages, s.name):
                d = state.stages.get(desc)
                if d and d.status in ("done", "approved", "awaiting_approval"):
                    d.status = "stale"
    if changed:
        st.save(run_dir, state)
    return changed


def _write_run_provenance(run_dir: Path, stages: list[Stage]) -> None:
    """Emit the run-dir replay/audit bundle (environment + provenance) at lifecycle end."""
    from core.llm import client as _llm
    from core.sr.orchestrator import provenance as prov
    state = st.load(run_dir)
    ts = state.created_utc
    prov.write_environment(run_dir, timestamp=ts)
    prov.write_provenance(run_dir, stage_log=_llm.stage_log(), run_state=state,
                          reproducibility={s.name: s.reproducibility for s in stages}, timestamp=ts)


def _ancestors(stages: list[Stage], target: str) -> set[str]:
    by = {s.name: s for s in stages}
    out: set[str] = set()
    stack = [target]
    while stack:
        for r in by[stack.pop()].requires:
            if r not in out:
                out.add(r)
                stack.append(r)
    return out


def run_to(cfg: SRConfig, target: str, *, created_utc: str, runs_dir: Path = RUNS_DIR,
           run_dir: Path | None = None, retmax: int | None = None,
           connector: object | None = None, do_extract: bool = True) -> Path:
    """Drive (auto) exactly the ancestor-closure of ``target`` to completion. Resumable:
    pass an existing ``run_dir`` to continue. Serialized per run dir by the run lock, and
    fails closed (RunBlocked) if the target does not reach a terminal successful state."""
    stages = build_registry(cfg)
    by_name = {s.name: s for s in stages}
    if target not in by_name:
        raise ValueError(f"unknown target stage {target!r}")
    if run_dir is None:
        run_dir = create_run(cfg, created_utc=created_utc, auto=True, runs_dir=runs_dir)
    needed = _ancestors(stages, target) | {target}
    subset = [s for s in stages if s.name in needed]
    with st.run_lock(run_dir):                      # serialize advancement per run dir
        while True:
            state = st.load(run_dir)
            if _refresh_staleness(run_dir, state, subset):   # detect upstream artifact edits
                state = st.load(run_dir)
            nxt = _next_stage(state, subset)
            if nxt is None:
                break
            run_stage(run_dir, nxt, cfg, retmax=retmax, connector=connector, do_extract=do_extract)
        _write_run_provenance(run_dir, stages)               # replay/audit bundle under runs/<id>
    # Fail closed: a blocked gate / failed stage must not look like a successful run_to.
    state = st.load(run_dir)
    tss = state.stages.get(target)
    status = tss.status if tss else "pending"
    if status not in ("done", "approved"):
        blocked = {n: s.status for n, s in state.stages.items()
                   if s.status in ("awaiting_approval", "failed", "stale", "running", "pending")}
        raise RunBlocked(f"run_to({target!r}) did not complete (status={status!r}); "
                         f"unfinished stages: {blocked}")
    for art in by_name[target].produces:
        if not (Path(run_dir) / art).exists():
            raise RunBlocked(f"target {target!r} reported {status!r} but output {art!r} is missing")
    return run_dir


def resume(run_dir: Path, cfg: SRConfig, target: str, *, created_utc: str = "") -> Path:
    """Continue an interrupted run to ``target``."""
    return run_to(cfg, target, created_utc=created_utc, run_dir=Path(run_dir))
