"""Evidence Synthesis Engine — guided CLI driver (``ese``).

Day-1 user surface over the orchestrator: init → status → next → approve → export.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.sr.config import load_sr_config
from core.sr.driver.plan import error_hint, reset_earliest_failed
from core.sr.driver.status import format_status, run_status
from core.sr.driver.summarize import summarize_stage
from core.sr.orchestrator import engine
from core.sr.orchestrator.stages import next_stage, topo_order


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cfg_for_run(run_dir, explicit_vertical: str | None):
    """Resolve the vertical for an existing run.

    Precedence: explicit --vertical flag > the vertical persisted in run_state.json
    > env/default (legacy runs). The resolved vertical is written back into
    ``os.environ["ESE_VERTICAL"]`` so every module that independently resolves the
    vertical from the environment (grade_config, rob, shared_or_vertical) agrees with
    the run — this is what prevents a run from being screened/graded against another
    review's PICO.
    """
    import os

    vertical = explicit_vertical
    if not vertical:
        try:
            from core.sr.orchestrator import state as st
            vertical = getattr(st.load(Path(run_dir)), "vertical", "") or None
        except Exception:
            vertical = None
    if vertical:
        os.environ["ESE_VERTICAL"] = vertical
        return load_sr_config(vertical)
    return load_sr_config()


def cmd_init(args) -> int:
    cfg = load_sr_config(args.vertical) if args.vertical else load_sr_config()
    run_id = args.run_id or uuid.uuid4().hex[:12]
    runs_dir = Path(args.runs_dir)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    engine.create_run(cfg, created_utc=_utc_now(), profile=args.profile,
                      auto=args.auto, runs_dir=runs_dir, run_id=run_id)
    if args.idea:
        (run_dir / "idea.txt").write_text(args.idea.strip() + "\n", encoding="utf-8")
    print(run_dir)
    return 0


def cmd_status(args) -> int:
    cfg = _cfg_for_run(args.run_dir, args.vertical)
    print(format_status(run_status(Path(args.run_dir), cfg=cfg)))
    return 0


def cmd_next(args) -> int:
    run_dir = Path(args.run_dir)
    cfg = _cfg_for_run(run_dir, args.vertical)
    from core.sr.orchestrator import state as st

    # Hold the run lock across the whole advance. It serializes concurrent `ese next`
    # calls (no duplicate stage runs) and — critically — makes the exclusive lease that
    # `ese recover` relies on actually held during a guided run, so recover cannot flip
    # a live `running` stage to failed. run_stage does not itself take the lock, so this
    # does not deadlock.
    with st.run_lock(run_dir):
        state = st.load(run_dir)
        stages = engine.build_registry(cfg, frontend=_frontend(run_dir))
        order = topo_order(stages, state.profile)
        reset_earliest_failed(run_dir, state, order)
        state = st.load(run_dir)
        nxt = next_stage(state, order)
        if nxt is None:
            blocked = [(n, s.status) for n, s in state.stages.items()
                       if s.status in ("awaiting_approval", "failed")]
            if blocked:
                name, status = blocked[0]
                print(f"Blocked on {name} ({status})")
                print(summarize_stage(run_dir, name))
                if status == "awaiting_approval":
                    print(f"\nApprove: ese approve {run_dir} {name} --actor \"Your Name\"")
            else:
                print("No pending stages — pipeline complete for this profile.")
            return 0
        print(f"Running stage: {nxt.name}")
        result = engine.run_stage(run_dir, nxt, cfg, retmax=args.retmax)
        print(f"Done: {nxt.name} — {result.summary}")
        state = st.load(run_dir)
        ss = state.stages.get(nxt.name)
        if ss and ss.status == "awaiting_approval":
            print(summarize_stage(run_dir, nxt.name))
            print(f"\nApprove: ese approve {run_dir} {nxt.name} --actor \"Your Name\"")
        return 0


def cmd_approve(args) -> int:
    engine.approve(Path(args.run_dir), args.stage, args.actor, ts_utc=args.ts or _utc_now())
    print(f"Approved {args.stage} as {args.actor}")
    return 0


def cmd_export(args) -> int:
    run_dir = Path(args.run_dir)
    # Fail closed on the artifact path: a run that carries a state file but no
    # persisted vertical (a pre-pinning legacy run) would otherwise silently export
    # under the default vertical and mislabel the manuscript's PICO. Require an
    # explicit --vertical rather than guessing.
    from core.sr.config import persisted_vertical
    if not args.vertical and (run_dir / "run_state.json").exists() \
            and persisted_vertical(run_dir) is None:
        print("Export refused: this run has no persisted vertical (created before "
              "vertical-pinning). Re-run with --vertical <name> to name the review "
              "explicitly, so the manuscript is not mislabeled.", file=sys.stderr)
        return 1
    cfg = _cfg_for_run(run_dir, args.vertical)
    gate = engine.export_gate(run_dir, cfg=cfg)
    if gate is not None and not gate[0]:
        print("Export BLOCKED:")
        for r in gate[1]:
            print(f"  - {r}")
        return 1
    if not args.approve_conclusions:
        print("Export requires --approve-conclusions \"Reviewer 2 Name\"")
        return 1

    import json

    from core.sr.manuscript.export import export_project
    from core.sr.profile import load_profile
    from core.tools.sr_io import (
        load_grade,
        load_rob,
        load_run_bundle,
        load_stats,
        modality_by_pmid,
        persist_reviewer_signoff,
    )

    # AI-drafted, citation-checked prose (produced by the `narrative` stage). Absent for
    # legacy/manual runs -> the manuscript emits the deterministic TODO stubs exactly as
    # before, so this remains additive.
    narrative = None
    references = None
    nar_path = run_dir / "narrative_final.json"
    if nar_path.exists():
        narrative = json.loads(nar_path.read_text(encoding="utf-8"))
        # Reference-integrity gate: fail CLOSED — BEFORE persisting a reviewer sign-off or
        # rendering — if the prose cites a broken/unverified reference. Surface as a clean
        # "Export BLOCKED" (return 1), matching export_gate above, rather than an uncaught
        # traceback that would also orphan a persisted sign-off. No-op when references.json
        # is absent (gate stays opt-in/dormant).
        from core.sr.references import ReferenceIntegrityError, gate_run_dir, load_library
        from core.sr.references_build import normalize_pmid_citations
        gate_text = normalize_pmid_citations(" ".join(str(v) for v in narrative.values()))
        try:
            gate_run_dir(run_dir, gate_text)
        except ReferenceIntegrityError as exc:
            print(f"Export BLOCKED (reference integrity): {exc}", file=sys.stderr)
            return 1
        ref_path = run_dir / "references.json"
        if ref_path.exists():
            references = load_library(ref_path)

    persist_reviewer_signoff(run_dir, args.approve_conclusions)
    bundle = load_run_bundle(run_dir, vertical=cfg.vertical)
    audit_path = run_dir / "honesty_audit.json"
    audit = None
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))

    from core.sr.orchestrator import state as st
    try:
        profile = load_profile(st.load(run_dir).profile)
    except FileNotFoundError:                      # legacy/no-state run -> default profile;
        profile = load_profile()                   # a real bad profile string now surfaces

    result = export_project(
        bundle.run, cfg, bundle.records, audit=audit,
        rob=load_rob(run_dir), grade=load_grade(run_dir),
        modality_by_pmid=modality_by_pmid(run_dir, cfg.subgroup_field), narrative=narrative,
        references=references, stats=load_stats(run_dir), profile=profile,
        gate=(True, []) if gate is not None else None,
    )
    print(f"Exported: {result.get('project_dir', result)}")
    return 0


def _load_dotenv(path: Path | None = None, *, secure: bool = False) -> None:
    """Load KEY=VALUE pairs from a gitignored .env without overriding existing env.

    When ``secure`` is set (the ~/.config/ese/providers.env secret file), enforce the
    same fail-closed checks as core.llm.gateway.load_key — refuse a symlink, refuse
    group/other-accessible perms, refuse a file not owned by the current user — so
    preloading the key does not silently bypass the gateway's secret-file hardening.
    """
    import os
    import sys

    root = Path(__file__).resolve().parents[3]
    env_path = path or (root / ".env")
    if not env_path.exists():
        return
    if secure:
        if env_path.is_symlink():
            print(f"warning: {env_path} is a symlink — refused (not loaded)", file=sys.stderr)
            return
        info = env_path.stat()
        if info.st_mode & 0o077:
            print(f"warning: {env_path} permissions too open (need 0600) — refused", file=sys.stderr)
            return
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            print(f"warning: {env_path} not owned by current user — refused", file=sys.stderr)
            return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def cmd_recover(args) -> int:
    """Reset stages stuck in ``running`` so the pipeline can resume.

    ``ese next`` already recovers a ``failed`` stage, but a worker killed mid-stage
    leaves the stage in ``running`` — which ``next`` neither retries nor reports,
    silently wedging the run. This resets any such stage to ``failed`` so the normal
    failed→pending retry path re-runs it.

    Done UNDER the run lock: holding the exclusive lease proves no live worker owns
    the run dir (a dead worker's flock is released by the OS), so resetting a
    ``running`` stage here is safe by construction. Additive — no existing behavior,
    the screening prompt, or its golden tests are touched.
    """
    run_dir = Path(args.run_dir)
    from core.sr.orchestrator import state as st

    reset: list[str] = []
    with st.run_lock(run_dir):
        state = st.load(run_dir)
        for name, ss in state.stages.items():
            if ss.status == "running":
                ss.status = "failed"
                ss.error_code = "recovered: stuck running (worker died mid-stage)"
                reset.append(name)
        if reset:
            st.save(run_dir, state)
    print(f"Recovered {len(reset)} stuck stage(s): {', '.join(reset) or 'none'}")
    return 0


def _frontend(run_dir: Path) -> bool:
    run_dir = Path(run_dir)
    return (run_dir / "idea.txt").exists() or (run_dir / "pico.json").exists()


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()                                                                    # project ./.env (if present)
    _load_dotenv(Path.home() / ".config" / "ese" / "providers.env", secure=True)      # documented key location (hardened)
    p = argparse.ArgumentParser(prog="ese", description="Evidence Synthesis Engine driver")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Create a new orchestrated run")
    init.add_argument("--vertical", help="Vertical name (default: ESE_VERTICAL / systematic-review)")
    init.add_argument("--profile", default="systematic", choices=["systematic", "scoping", "rapid", "living"])
    init.add_argument("--idea", help="Rough research idea (enables idea→protocol front-end)")
    init.add_argument("--run-id", help="Optional run id (default: random)")
    init.add_argument("--runs-dir", default="runs")
    init.add_argument("--auto", action="store_true", help="Auto mode (skips optional gates, not honesty gates)")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="Show run progress and blocked gates")
    status.add_argument("run_dir")
    status.add_argument("--vertical")
    status.set_defaults(func=cmd_status)

    nxt = sub.add_parser("next", help="Run the next pending stage")
    nxt.add_argument("run_dir")
    nxt.add_argument("--vertical")
    nxt.add_argument("--retmax", type=int)
    nxt.set_defaults(func=cmd_next)

    appr = sub.add_parser("approve", help="Sign off a honesty gate")
    appr.add_argument("run_dir")
    appr.add_argument("stage")
    appr.add_argument("--actor", required=True)
    appr.add_argument("--ts", help="UTC timestamp (default: now)")
    appr.set_defaults(func=cmd_approve)

    exp = sub.add_parser("export", help="Export manuscript (fail-closed via export_gate)")
    exp.add_argument("run_dir")
    exp.add_argument("--vertical")
    exp.add_argument("--approve-conclusions", required=True, help="Reviewer 2 name")
    exp.set_defaults(func=cmd_export)

    rec = sub.add_parser("recover", help="Reset stuck 'running' stages (dead worker) so they re-run")
    rec.add_argument("run_dir")
    rec.set_defaults(func=cmd_recover)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except engine.RunBlocked as exc:
        print(f"Run blocked: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        from core.llm.gateway import MissingKeyError

        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, MissingKeyError):
            print(error_hint("MissingKeyError"), file=sys.stderr)
        else:
            from core.sr.orchestrator import state as st

            try:
                run_dir = getattr(args, "run_dir", None)
                stage = getattr(args, "stage", None)
                if run_dir:
                    state = st.load(Path(run_dir))
                    ss = state.stages.get(stage) if stage else None
                    if not ss:
                        for name, sst in state.stages.items():
                            if sst.status == "failed":
                                ss = sst
                                stage = name
                                break
                    if ss and ss.status == "failed" and ss.error_code:
                        hint = error_hint(ss.error_code)
                        if hint:
                            print(hint, file=sys.stderr)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
