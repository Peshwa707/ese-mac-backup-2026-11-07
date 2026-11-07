"""The one-tool-at-a-time command line — the first human surface (mode B).

    python -m core.tools list
    python -m core.tools describe screen
    python -m core.tools run search  runs/review --vertical <name> --retmax 200
    python -m core.tools run screen  runs/review --vertical <name> --cloud
    python -m core.tools run pool-proportions runs/review --vertical <name> --field <outcome>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from core.tools.harness import HarnessWorkPending
from core.tools import recipes, registry, runner
from core.tools.posture import active_posture, resolve_backend


def _brain(tool) -> str:
    if tool.kind != "ai":
        return "math (local, free)"
    harness = "+harness" if tool.backend.harness_enabled else ""
    return "cloud-only" if tool.backend.cloud_only else f"default {tool.backend.default}{harness}"


def _cmd_list() -> None:
    print(f"Posture: {active_posture()}\n")
    print(f"{'TOOL':<20} {'KIND':<14} {'BRAIN':<22} {'LOCK':<11} VALIDATED")
    for t in registry.all_tools():
        sc = t.scorecard
        status = ("ext+int" if sc.trusted_for_agent else
                  "internal" if sc.internally_tested else "—")
        local = " (local-ready)" if t.backend.local_validated else ""
        lock = "conclusion" if t.conclusion_bearing else "—"
        print(f"{t.name:<20} {t.kind:<14} {_brain(t) + local:<22} {lock:<11} {status}")


def _cmd_describe(name: str) -> None:
    t = registry.get(name)
    print(f"{t.name} — {t.description}")
    print(f"  kind:    {t.kind}")
    print(f"  brain:   {_brain(t)}")
    if t.kind == "ai":
        print(f"  harness: {'enabled' if t.backend.harness_enabled else 'not supported'}")
    print(f"  reads:   {', '.join(t.input_artifacts) or '(nothing)'}")
    print(f"  writes:  {t.output_artifact}")
    print(f"  args:    {t.args_help or '(none)'}")
    print(f"  conclusion_bearing: {t.conclusion_bearing}")
    print(f"  scorecard: internally_tested={t.scorecard.internally_tested} "
          f"externally_validated={t.scorecard.externally_validated} "
          f"agent-trusted={t.scorecard.trusted_for_agent}")
    if t.kind == "ai":
        print(f"  -> would run on: {resolve_backend(t)} (under posture '{active_posture()}')")


def _cmd_run(args) -> None:
    tool_args = _shared_args(args)
    override = "harness" if args.harness else "local" if args.local else "cloud" if args.cloud else None
    out = runner.run_tool(args.name, Path(args.run_dir), tool_args, override=override)
    print(f"wrote {out}")


def _shared_args(args) -> dict:
    return {k: v for k, v in (("vertical", args.vertical), ("retmax", args.retmax),
                              ("field", args.field), ("measure", args.measure),
                              ("method", args.method), ("outcome", args.outcome),
                              ("moderator", args.moderator), ("min_k", args.min_k),
                              ("approve_conclusions", args.approve_conclusions),
                              ("profile", args.profile), ("base_dir", args.base_dir),
                              ("day", args.day), ("dest", args.dest),
                              ("overlay_only", args.overlay_only),
                              ("concurrency", args.concurrency),
                              ("harness_source", args.harness_source))
                 if v is not None}


def _cmd_recipe_list() -> None:
    print(f"{'RECIPE':<16} STEPS")
    for r in recipes.all_recipes():
        print(f"{r.name:<16} {' -> '.join(s.tool for s in r.steps)}")


def _cmd_recipe_run(args) -> None:
    override = "harness" if args.harness else "local" if args.local else "cloud" if args.cloud else None
    outputs = recipes.run_recipe(args.name, Path(args.run_dir), _shared_args(args), override=override)
    for out in outputs:
        print(f"wrote {out}")


def _add_shared_run_args(parser) -> None:
    parser.add_argument("--vertical"); parser.add_argument("--retmax", type=int); parser.add_argument("--field")
    parser.add_argument("--measure"); parser.add_argument("--method")
    parser.add_argument("--outcome"); parser.add_argument("--moderator"); parser.add_argument("--min-k", type=int)
    parser.add_argument("--approve-conclusions", help="record Reviewer-2 signoff before conclusion-bearing rendering")
    parser.add_argument("--profile"); parser.add_argument("--base-dir"); parser.add_argument("--day")
    parser.add_argument("--dest"); parser.add_argument("--overlay-only", action="store_true")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--harness-source", choices=("claude", "gpt"), default=None)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--local", action="store_true", help="force the local brain (if allowed)")
    g.add_argument("--cloud", action="store_true", help="force the cloud brain")
    g.add_argument("--harness", action="store_true", help="write a harness request and pause for an agent")


def main(argv: list[str] | None = None) -> None:
    registry.load_builtins()
    p = argparse.ArgumentParser(prog="evidence-tools", description="Run evidence tools one at a time.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all tools + their maturity")
    d = sub.add_parser("describe", help="show a tool's contract"); d.add_argument("name")
    r = sub.add_parser("run", help="run one tool over a question's run-folder")
    r.add_argument("name"); r.add_argument("run_dir")
    _add_shared_run_args(r)
    recipe = sub.add_parser("recipe", help="list or run fixed tool recipes")
    recipe_sub = recipe.add_subparsers(dest="recipe_cmd", required=True)
    recipe_sub.add_parser("list", help="list recipes")
    rr = recipe_sub.add_parser("run", help="run one recipe over a question's run-folder")
    rr.add_argument("name"); rr.add_argument("run_dir")
    _add_shared_run_args(rr)
    a = p.parse_args(argv)
    if a.cmd == "list":
        _cmd_list()
    elif a.cmd == "describe":
        _cmd_describe(a.name)
    elif a.cmd == "run":
        try:
            _cmd_run(a)
        except HarnessWorkPending as exc:
            print(exc)
        except (FileNotFoundError, KeyError, ValueError, ValidationError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    elif a.cmd == "recipe":
        if a.recipe_cmd == "list":
            _cmd_recipe_list()
        elif a.recipe_cmd == "run":
            try:
                _cmd_recipe_run(a)
            except HarnessWorkPending as exc:
                print(exc)
            except (FileNotFoundError, KeyError, ValueError, ValidationError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
