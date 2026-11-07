"""Command-line entry point for the Evidence Engine.

Usage
-----
    evidence-engine --case-text "De-identified case text here..." [--manuscript] [options]
    evidence-engine --case-file /path/to/case.txt [--manuscript] [options]

Run ``evidence-engine --help`` for the full option reference.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import date
from pathlib import Path

from core.paths import ENGINE_BASE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename_part(s: str, max_len: int = 20) -> str:
    """Reduce an arbitrary string to a filesystem-safe slug (not used for IDs)."""
    slug = re.sub(r"[^\w-]", "_", s)
    return slug[:max_len]


def _check_api_key() -> None:
    """Exit non-zero with a clear message if ANTHROPIC_API_KEY is unset."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set. "
            "Export it before running the engine:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence-engine",
        description=(
            "Evidence Engine — grounded clinical decision support from a "
            "de-identified case. Every claim is PMID/NCT-cited; PHI is "
            "hard-stopped before any external call."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  evidence-engine --case-text 'De-identified note...' --manuscript\n"
            "  evidence-engine --case-file /path/to/case.txt --format html\n"
            "\n"
            "Environment:\n"
            "  ANTHROPIC_API_KEY       Required — LLM stages (structure, synthesis, etc.)\n"
            "  NCBI_API_KEY            Optional — higher PubMed rate limit\n"
            "  EVIDENCE_VERTICAL       Active vertical (default: oncology) [alias: ONCO_VERTICAL]\n"
            "  EVIDENCE_PROVENANCE_DIR Provenance log dir (default: /tmp/evidence-provenance)\n"
        ),
    )

    # Case input — mutually exclusive, one required
    case_group = parser.add_mutually_exclusive_group(required=True)
    case_group.add_argument(
        "--case-text",
        metavar="TEXT",
        help="De-identified case text as a string.",
    )
    case_group.add_argument(
        "--case-file",
        metavar="PATH",
        type=Path,
        help="Path to a file containing the de-identified case text.",
    )

    parser.add_argument(
        "--manuscript",
        action="store_true",
        default=False,
        help="Also generate a CARE-compliant case-report manuscript (requires findings).",
    )

    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        type=Path,
        default=ENGINE_BASE / "case-reports",
        help="Directory for output files (default: ~/Documents/EvidenceEngine/case-reports/).",
    )

    parser.add_argument(
        "--format",
        choices=["html", "json", "pdf", "all"],
        default="all",
        help=(
            "Output format(s). pdf degrades to html if no Chrome/Chromium is "
            "available (default: all)."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Core render dispatch
# ---------------------------------------------------------------------------

def _render_and_write(
    report,
    vertical,
    formats: list[str],
    output_dir: Path,
    make_manuscript: bool,
) -> list[Path]:
    """Render requested formats and write to output_dir. Returns list of written paths."""
    from core.render.render import render_html, render_json, render_pdf, pdf_available

    today = date.today().isoformat()
    short_id = report.id[:8]
    label = (getattr(vertical, "name", "") or "case").replace("-", "_")
    stem = f"{label}_case_report_{today}_{short_id}"

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if "html" in formats or "all" in formats:
        html_content = render_html(report, vertical, manuscript=make_manuscript)
        path = output_dir / f"{stem}.html"
        path.write_text(html_content, encoding="utf-8")
        written.append(path)

    if "json" in formats or "all" in formats:
        json_content = render_json(report)
        path = output_dir / f"{stem}.json"
        path.write_text(json_content, encoding="utf-8")
        written.append(path)

    if "pdf" in formats or "all" in formats:
        if pdf_available():
            pdf_bytes = render_pdf(report, vertical, manuscript=make_manuscript)
            path = output_dir / f"{stem}.pdf"
            path.write_bytes(pdf_bytes)
            written.append(path)
        else:
            # Degrade gracefully — write HTML instead, warn the user.
            print(
                "WARNING: No Chrome/Chromium binary found; PDF output degraded to HTML.",
                file=sys.stderr,
            )
            html_content = render_html(report, vertical, manuscript=make_manuscript)
            path = output_dir / f"{stem}_pdf_degraded.html"
            path.write_text(html_content, encoding="utf-8")
            written.append(path)

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Guard: API key must be present before doing any work.
    _check_api_key()

    # Resolve case text.
    if args.case_text is not None:
        raw_text = args.case_text
    else:
        case_path: Path = args.case_file.expanduser().resolve()
        if not case_path.exists():
            print(f"ERROR: case file not found: {case_path}", file=sys.stderr)
            sys.exit(1)
        raw_text = case_path.read_text(encoding="utf-8")

    if not raw_text.strip():
        print("ERROR: case text is empty.", file=sys.stderr)
        sys.exit(1)

    # Lazy imports — only pay the import cost after arg parsing succeeds.
    from core.pipeline import PHIDetectedError, run_case
    from core.config.vertical import load_vertical

    # Run the pipeline.
    try:
        report = asyncio.run(
            run_case(raw_text, make_manuscript=args.manuscript)
        )
    except PHIDetectedError:
        print(
            "ERROR: Case rejected — residual PHI detected after de-identification. "
            "Please remove all patient-identifying information before submitting.",
            file=sys.stderr,
        )
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)

    # Render outputs.
    vertical = load_vertical()
    formats = [args.format]  # e.g. ["all"] or ["html"]

    try:
        written = _render_and_write(
            report,
            vertical,
            formats,
            args.output_dir.expanduser().resolve(),
            args.manuscript,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: rendering failed: {exc}", file=sys.stderr)
        sys.exit(3)

    # Summary line.
    n_findings = len(report.decision_support)
    has_manuscript = report.manuscript is not None
    provenance_id = report.provenance_id or report.id

    print(
        f"Report complete — {n_findings} finding(s), "
        f"manuscript={'yes' if has_manuscript else 'no'}, "
        f"provenance_id={provenance_id}"
    )
    for path in written:
        print(f"  -> {path}")

    if report.empty_reason:
        print(f"  NOTE: {report.empty_reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
