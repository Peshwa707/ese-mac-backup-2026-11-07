"""Run one tool over a question's run-folder.

Checks the tool's input files exist (clear error if you skipped an upstream step),
resolves which brain to use, pins that brain for the duration of the call, runs the
tool, and returns the path it wrote. Tools are stateless functions over the folder —
which is what lets a human inspect or hand-edit a file between steps.
"""

from __future__ import annotations

from pathlib import Path

from core.tools import registry
from core.tools.posture import resolve_backend
from core.tools.spec import Tool


def run_tool(name: str, run_dir: Path, args: dict | None = None,
             override: str | None = None, posture: str | None = None) -> Path:
    tool = registry.get(name)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    args = dict(args or {})

    # Honesty-gate enforcement for conclusion-bearing tools (grade/narrative/manuscript):
    # a blocked orchestrated run must fail CLOSED here, BEFORE the input-artifacts check —
    # an incomplete unapproved run is missing those artifacts anyway, so without this the
    # confusing FileNotFoundError below would mask the real reason and (for `manuscript`)
    # skip the stale-dossier purge. None = legacy/ungated run (no run_state.json).
    if tool.conclusion_bearing:
        from core.sr.orchestrator.engine import export_gate
        gate = export_gate(run_dir)
        if gate is not None and gate[0] is False:
            if tool.output_artifact == "manuscript.json":
                from core.sr.config import load_sr_config
                from core.sr.manuscript.export import write_blocked_export
                from core.sr.profile import load_profile
                write_blocked_export(
                    load_sr_config(args.get("vertical")), gate[1],
                    base_dir=Path(args["base_dir"]) if args.get("base_dir") else None,
                    day=args.get("day"), review_type=load_profile(args.get("profile")).name)
            raise RuntimeError(f"tool '{name}' blocked — run has not cleared its honesty "
                               "gates: " + "; ".join(gate[1]))

    missing = [a for a in tool.input_artifacts if not (run_dir / a).exists()]
    if missing:
        raise FileNotFoundError(
            f"tool '{name}' needs {missing} in {run_dir} — run the upstream tool(s) first")
    reviewer = args.pop("approve_conclusions", None)
    if reviewer and tool.conclusion_bearing:
        from core.tools.sr_io import persist_reviewer_signoff
        persist_reviewer_signoff(run_dir, reviewer)

    backend = resolve_backend(tool, override=override, posture=posture)
    if tool.kind == "ai" and backend != "harness":
        from core.llm import client
        client.set_forced_backend(backend)
        try:
            out = tool.run(run_dir, args, backend)
        finally:
            client.clear_forced_backend()
    else:
        out = tool.run(run_dir, args, backend)
    return Path(out)


def plan_backend(name: str, override: str | None = None, posture: str | None = None) -> str:
    """What brain WOULD this tool use (without running it)? For `describe` / dry-runs."""
    return resolve_backend(registry.get(name), override=override, posture=posture)
