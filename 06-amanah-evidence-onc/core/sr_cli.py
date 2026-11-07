"""CLI for the systematic-review slice (Milestone 1).

    python -m core.sr_cli                 # live run with config defaults
    python -m core.sr_cli --retmax 60     # smaller corpus
    python -m core.sr_cli --no-extract    # search + screen only (cheaper)

Writes the PRISMA flow diagram, extraction table, screening reconciliation sheet,
and run JSON to ~/Documents/EvidenceEngine/<vertical>/<id>/ (override the root with
EVIDENCE_OUTPUT_DIR) and the repo runs/<id>/.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from core.sr.config import load_sr_config
from core.sr.pipeline import run_sr
from core.sr.render import write_all


def _check_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set — screening/extraction need it.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sr-engine",
        description="Systematic-review engine — Milestone 1 (live search → screen → extract).",
    )
    p.add_argument("--retmax", type=int, default=None, help="Override PubMed retmax for this run.")
    p.add_argument("--no-extract", action="store_true", help="Skip extraction (search + screen only).")
    p.add_argument(
        "--approve-conclusions", metavar="REVIEWER", default=None,
        help="Record a human Reviewer-2 sign-off (name/initials) that UNLOCKS final "
             "conclusions + GRADE certainty for rendering. Without it they render as "
             "'DRAFT — awaiting Reviewer 2'.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Extra output dir (default: ~/Documents/EvidenceEngine/<vertical>/<id>/ + repo runs/<id>/).",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _check_api_key()

    cfg = load_sr_config()
    print(f"Running systematic-review slice: {cfg.review_title}", file=sys.stderr)
    print(f"PubMed query (retmax={args.retmax or cfg.retmax}) — executing live…", file=sys.stderr)

    run = asyncio.run(run_sr(cfg, retmax=args.retmax, do_extract=not args.no_extract))

    if args.approve_conclusions:
        from core.sr.signoff import approve
        so = approve(run, args.approve_conclusions)
        print(f"Conclusions UNLOCKED by Reviewer 2: {so.reviewer} ({so.timestamp})", file=sys.stderr)
    else:
        print("Conclusions LOCKED (DRAFT) — re-run with --approve-conclusions \"<name>\" "
              "after human review to finalize.", file=sys.stderr)

    from core.paths import vertical_dir

    repo_root = Path(__file__).resolve().parents[1]
    out_dirs = [
        vertical_dir(cfg.root.name) / cfg.short_id,
        repo_root / "runs" / cfg.short_id,
    ]
    if args.output_dir:
        out_dirs.append(args.output_dir.expanduser())

    written = write_all(run, cfg, out_dirs)

    c = run.counts
    print(
        f"\nDone. identified={c.identified_pubmed} dedup={c.after_dedup} "
        f"verified={c.screened} (dropped {c.id_unverified_removed} unverifiable) "
        f"include={c.included} exclude={c.excluded_screening} uncertain={c.uncertain}"
    )
    print(f"provenance: {run.provenance_id}")
    for p in written:
        print(f"  -> {p}")


if __name__ == "__main__":
    main()
