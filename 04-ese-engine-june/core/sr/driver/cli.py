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
    cfg = load_sr_config(args.vertical) if args.vertical else None
    print(format_status(run_status(Path(args.run_dir), cfg=cfg)))
    return 0


def cmd_next(args) -> int:
    run_dir = Path(args.run_dir)
    cfg = load_sr_config(args.vertical) if args.vertical else load_sr_config()
    from core.sr.orchestrator import state as st

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
    cfg = load_sr_config(args.vertical) if args.vertical else load_sr_config()
    gate = engine.export_gate(run_dir, cfg=cfg)
    if gate is not None and not gate[0]:
        print("Export BLOCKED:")
        for r in gate[1]:
            print(f"  - {r}")
        return 1
    if not args.approve_conclusions:
        print("Export requires --approve-conclusions \"Reviewer-of-record Name\"")
        return 1

    from core.sr.manuscript.export import export_project
    from core.tools.sr_io import load_run_bundle, persist_reviewer_signoff

    persist_reviewer_signoff(run_dir, args.approve_conclusions)
    bundle = load_run_bundle(run_dir, vertical=str(cfg.root))
    audit_path = run_dir / "honesty_audit.json"
    audit = None
    if audit_path.exists():
        import json
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    result = export_project(
        bundle.run, cfg, bundle.records, audit=audit,
        gate=(True, []) if gate is not None else None,
    )
    print(f"Exported: {result.get('project_dir', result)}")
    return 0


def _load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from a gitignored .env without overriding existing env."""
    import os

    root = Path(__file__).resolve().parents[3]
    env_path = path or (root / ".env")
    if not env_path.exists():
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


def _frontend(run_dir: Path) -> bool:
    run_dir = Path(run_dir)
    return (run_dir / "idea.txt").exists() or (run_dir / "pico.json").exists()


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
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
    exp.add_argument("--approve-conclusions", required=True, help="reviewer-of-record name")
    exp.set_defaults(func=cmd_export)

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
