"""CLI for offline benchmark scaffolding."""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmark.answer_key import draft_answer_key
from benchmark.driver import create_run_plan
from benchmark.frame import build_sampling_frame
from benchmark.harness_fulfillment import status_report
from benchmark.report import aggregate_scores
from benchmark.score import score_run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="benchmark", description="Offline benchmark scaffolding.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    frame = sub.add_parser("frame")
    frame_sub = frame.add_subparsers(dest="frame_cmd", required=True)
    frame_build = frame_sub.add_parser("build")
    frame_build.add_argument("--input", type=Path, required=True)
    frame_build.add_argument("--out", type=Path, required=True)

    answer = sub.add_parser("answer-key")
    answer_sub = answer.add_subparsers(dest="answer_cmd", required=True)
    answer_draft = answer_sub.add_parser("draft")
    answer_draft.add_argument("--case", type=Path, required=True)
    answer_draft.add_argument("--out", type=Path, required=True)

    score = sub.add_parser("score")
    score_sub = score.add_subparsers(dest="score_cmd", required=True)
    score_run_cmd = score_sub.add_parser("run")
    score_run_cmd.add_argument("--run-dir", type=Path, required=True)
    score_run_cmd.add_argument("--answer-key", type=Path, required=True)
    score_run_cmd.add_argument("--out", type=Path, required=True)
    score_run_cmd.add_argument(
        "--allow-draft",
        action="store_true",
        help="score an unsealed draft answer key with a prominent scaffold-only caveat",
    )

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report_cmd", required=True)
    aggregate = report_sub.add_parser("aggregate")
    aggregate.add_argument("--scores", type=Path, nargs="+", required=True)
    aggregate.add_argument("--out-json", type=Path, required=True)
    aggregate.add_argument("--out-md", type=Path, required=True)

    driver = sub.add_parser("driver")
    driver_sub = driver.add_subparsers(dest="driver_cmd", required=True)
    plan = driver_sub.add_parser("plan")
    plan.add_argument("--case", type=Path, required=True)
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--harness-source", choices=("claude", "gpt"), default="claude")

    h = sub.add_parser("harness")
    h_sub = h.add_subparsers(dest="harness_cmd", required=True)
    h_status = h_sub.add_parser("status")
    h_status.add_argument("--run-dir", type=Path, required=True)
    h_status.add_argument("--source", choices=("claude", "gpt"), required=True)

    args = parser.parse_args(argv)
    if args.cmd == "frame" and args.frame_cmd == "build":
        out = build_sampling_frame(args.input, args.out)
        print(f"wrote {args.out} ({len(out.candidates)} candidates)")
    elif args.cmd == "answer-key" and args.answer_cmd == "draft":
        out = draft_answer_key(args.case, args.out)
        print(f"wrote {args.out} ({len(out.outcomes)} draft outcomes)")
    elif args.cmd == "score" and args.score_cmd == "run":
        out = score_run(args.run_dir, args.answer_key, args.out, allow_draft=args.allow_draft)
        print(f"wrote {args.out} ({len(out.outcomes)} outcome scores)")
    elif args.cmd == "report" and args.report_cmd == "aggregate":
        out = aggregate_scores(args.scores, args.out_json, args.out_md)
        print(f"wrote {args.out_json} and {args.out_md} ({out['n_cases']} cases)")
    elif args.cmd == "driver" and args.driver_cmd == "plan":
        out = create_run_plan(args.case, args.out, harness_source=args.harness_source)
        print(f"wrote {args.out} ({len(out.commands)} commands)")
    elif args.cmd == "harness" and args.harness_cmd == "status":
        print(status_report(args.run_dir, args.source).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
