"""Compile + validate a vertical from a ProtocolSpec (or draft via the native LLM path).

    python -m core.sr.protocol --spec spec.json --name diet-cvd [--check-count]
    python -m core.sr.protocol --draft draft.txt --name diet-cvd   # needs ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.sr.config import vertical_root
from core.sr.protocol.compile import compile_vertical, render_protocol_md
from core.sr.protocol.spec import ProtocolSpec
from core.sr.protocol.validate import validate_vertical

_SYSTEM = (
    "You are a systematic-review methodologist. Convert the user's rough draft into a complete, "
    "valid ProtocolSpec. Use PECO/PICO framing; write a high-recall but specific PubMed concept "
    "set (population, exposure/intervention, design blocks); choose the review profile and RoB "
    "tool from the study designs (observational -> nos). Every outcome that should be meta-"
    "analysed must set pool_field to an extraction field name you also define. Do not invent "
    "evidence or results; where the draft is silent, choose a sensible default."
)


def _spec_from_draft(draft: str) -> ProtocolSpec:
    from core.llm.client import parse
    return parse(ProtocolSpec, _SYSTEM, f"Rough draft:\n\n{draft}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sr-protocol")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--spec", type=Path, help="ProtocolSpec JSON to compile.")
    src.add_argument("--draft", type=Path, help="Rough-draft text (native LLM path; needs API key).")
    ap.add_argument("--name", required=True, help="Vertical directory name to write.")
    ap.add_argument("--dest", type=Path, default=None, help="Override verticals/<name>/.")
    ap.add_argument("--check-count", action="store_true", help="Live PubMed count sanity check.")
    args = ap.parse_args(argv)

    if args.spec:
        spec = ProtocolSpec.model_validate_json(args.spec.read_text())
    else:
        spec = _spec_from_draft(args.draft.read_text())

    dest = (args.dest or vertical_root(args.name)).expanduser()
    written = compile_vertical(spec, dest)
    (dest / "protocol.md").write_text(render_protocol_md(spec), encoding="utf-8")
    report = validate_vertical(args.name, root=dest, check_count=args.check_count)
    (dest / "validation.json").write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    print(f"vertical '{args.name}' -> {dest}")
    for p in written:
        print(f"  wrote {p.name}")
    print("  wrote protocol.md, validation.json")
    print(f"valid: {report.ok}" + (f"  | PubMed count: {report.pubmed_count}"
                                   if report.pubmed_count is not None else ""))
    for e in report.errors:
        print(f"  ERROR: {e}", file=sys.stderr)
    for w in report.warnings:
        print(f"  warn: {w}")
    if not report.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
