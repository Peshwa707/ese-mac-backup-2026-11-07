#!/usr/bin/env python3
"""Render the Claude secondary AI review Markdown as a polished PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

import render_review_pdf as base


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "review_outputs"
    / "neurodevelopmental-environmental-mismatch-20260729"
    / "claude_ai_reviewer_2"
)


def file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def cover_story(style_map, toc, provenance: dict[str, object]):
    screening = provenance["screening"]
    status_box = Table(
        [
            [
                Paragraph(
                    "<b>BLINDED SECONDARY AI REVIEW</b><br/>"
                    "Claude output sealed before Codex comparison",
                    style_map["quote"],
                )
            ]
        ],
        colWidths=[5.8 * inch],
    )
    status_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), base.PALE_AMBER),
                ("BOX", (0, 0), (-1, -1), 0.8, base.AMBER),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    metadata = [
        [
            Paragraph(
                f"<b>Screened</b><br/>{sum(int(value) for value in screening.values()):,} occurrences",
                style_map["small"],
            ),
            Paragraph(
                f"<b>Advanced</b><br/>{int(screening.get('include', 0)) + int(screening.get('uncertain', 0)):,}",
                style_map["small"],
            ),
            Paragraph(
                f"<b>Excluded</b><br/>{int(screening.get('exclude', 0)):,}",
                style_map["small"],
            ),
        ],
        [
            Paragraph(
                f"<b>Extracted</b><br/>{int(provenance['extracted_occurrences']):,} occurrences",
                style_map["small"],
            ),
            Paragraph(
                f"<b>Verified sources</b><br/>{int(provenance['unique_verified_pmids']):,} PMIDs",
                style_map["small"],
            ),
            Paragraph(
                f"<b>Claims</b><br/>{int(provenance['claim_assessments']):,}",
                style_map["small"],
            ),
        ],
    ]
    meta_table = Table(metadata, colWidths=[1.93 * inch] * 3)
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), base.PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.6, base.RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, base.RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    toc.levelStyles = [style_map["toc0"], style_map["toc1"]]
    return [
        Spacer(1, 0.62 * inch),
        Paragraph("EVIDENCE SYNTHESIS REVIEW", style_map["cover_label"]),
        Spacer(1, 0.18 * inch),
        Paragraph(
            "Adaptive-Constraint and Environmental-Mismatch Model of Adult "
            "Neurodevelopmental Functioning",
            style_map["cover_title"],
        ),
        Paragraph(
            "Claude Blinded Secondary AI Review",
            style_map["cover_subtitle"],
        ),
        Spacer(1, 0.18 * inch),
        status_box,
        Spacer(1, 0.32 * inch),
        meta_table,
        Spacer(1, 0.4 * inch),
        Paragraph(
            "AI Reviewer 2 - not Human Reviewer 2, diagnosis, treatment guidance, "
            "or clinical sign-off",
            ParagraphStyle(
                "ClaudeCoverFootnote",
                parent=style_map["small"],
                alignment=TA_CENTER,
                textColor=base.MUTED,
            ),
        ),
        PageBreak(),
        Paragraph("Contents", style_map["toc_title"]),
        toc,
        PageBreak(),
    ]


def draw_cover_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(base.TEAL)
    canvas.setLineWidth(4)
    canvas.line(0.72 * inch, 10.45 * inch, 7.78 * inch, 10.45 * inch)
    canvas.setFont("ReviewSans", 7)
    canvas.setFillColor(base.MUTED)
    canvas.drawCentredString(
        4.25 * inch,
        0.45 * inch,
        "Confidential working draft - human and clinical review required",
    )
    canvas.restoreState()


def draw_later_page(canvas, doc):
    canvas.saveState()
    width, height = letter
    canvas.setStrokeColor(base.RULE)
    canvas.setLineWidth(0.5)
    canvas.line(
        0.72 * inch,
        height - 0.52 * inch,
        width - 0.72 * inch,
        height - 0.52 * inch,
    )
    canvas.setFont("ReviewSans-Bold", 6.8)
    canvas.setFillColor(base.NAVY)
    canvas.drawString(
        0.72 * inch,
        height - 0.38 * inch,
        "CLAUDE SECONDARY AI EVIDENCE REVIEW",
    )
    canvas.setFont("ReviewSans", 6.5)
    canvas.setFillColor(base.AMBER)
    canvas.drawRightString(
        width - 0.72 * inch,
        height - 0.38 * inch,
        "HUMAN REVIEW + CLINICAL SIGN-OFF PENDING",
    )
    canvas.setStrokeColor(base.RULE)
    canvas.line(0.72 * inch, 0.55 * inch, width - 0.72 * inch, 0.55 * inch)
    canvas.setFont("ReviewSans", 6.7)
    canvas.setFillColor(base.MUTED)
    canvas.drawString(
        0.72 * inch,
        0.37 * inch,
        "Claude AI Reviewer 2 - abstract-level rapid evidence-review draft",
    )
    canvas.drawRightString(
        width - 0.72 * inch,
        0.37 * inch,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    source = output / "systematic_evidence_review_full.md"
    target = output / "systematic_evidence_review_full.pdf"
    provenance_path = output / "secondary_ai_reviewer_provenance.json"
    if not source.is_file() or not provenance_path.is_file():
        raise FileNotFoundError("Claude review Markdown/provenance is missing")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))

    base.register_fonts()
    style_map = base.styles()
    document = base.ReviewDocTemplate(
        str(target),
        pagesize=letter,
        rightMargin=0.72 * inch,
        leftMargin=0.72 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.68 * inch,
        title=(
            "Adaptive-Constraint and Environmental-Mismatch Model - "
            "Claude Secondary AI Review"
        ),
        author="Claude AI Reviewer 2; human and clinical review pending",
        subject="Blinded rapid systematic evidence review",
    )
    toc = TableOfContents()
    story = cover_story(style_map, toc, provenance)
    story.extend(
        base.markdown_story(
            source.read_text(encoding="utf-8"),
            style_map,
            letter[0] - document.leftMargin - document.rightMargin,
        )
    )
    document.multiBuild(
        story,
        onFirstPage=draw_cover_page,
        onLaterPages=draw_later_page,
    )
    provenance["pdf_render"] = {
        "rendered_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_markdown": file_record(source),
        "rendered_pdf": file_record(target),
        "renderer_script": file_record(Path(__file__).resolve()),
        "reportlab_version": reportlab.Version,
        "human_reviewer_2": "pending",
        "clinical_sign_off": "pending",
    }
    artifacts = provenance.setdefault("artifacts", {})
    artifacts[target.name] = {
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
