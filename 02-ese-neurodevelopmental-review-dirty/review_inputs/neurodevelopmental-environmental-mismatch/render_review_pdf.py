#!/usr/bin/env python3
"""Render the audited systematic-review Markdown to a polished PDF."""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "review_outputs" / "neurodevelopmental-environmental-mismatch-20260729"
SOURCE = OUTPUT / "systematic_evidence_review_full.md"
TARGET = OUTPUT / "systematic_evidence_review_full.pdf"

NAVY = colors.HexColor("#17324D")
TEAL = colors.HexColor("#1A6A73")
PALE_TEAL = colors.HexColor("#EAF5F5")
PALE_AMBER = colors.HexColor("#FFF5DA")
AMBER = colors.HexColor("#A45A00")
INK = colors.HexColor("#202833")
MUTED = colors.HexColor("#5D6A76")
RULE = colors.HexColor("#D6DEE5")
PALE_BLUE = colors.HexColor("#F1F6FA")


def register_fonts() -> None:
    base = Path("/System/Library/Fonts/Supplemental")
    pdfmetrics.registerFont(TTFont("ReviewSans", str(base / "Arial.ttf")))
    pdfmetrics.registerFont(TTFont("ReviewSans-Bold", str(base / "Arial Bold.ttf")))
    pdfmetrics.registerFont(TTFont("ReviewSans-Italic", str(base / "Arial Italic.ttf")))
    pdfmetrics.registerFont(
        TTFont("ReviewSans-BoldItalic", str(base / "Arial Bold Italic.ttf"))
    )
    pdfmetrics.registerFontFamily(
        "ReviewSans",
        normal="ReviewSans",
        bold="ReviewSans-Bold",
        italic="ReviewSans-Italic",
        boldItalic="ReviewSans-BoldItalic",
    )


def normalize(text: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u2009": " ",
        "\u202f": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2192": "->",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def inline(text: str) -> str:
    text = normalize(text)
    placeholders: dict[str, str] = {}

    def protect_link(match: re.Match[str]) -> str:
        token = f"@@LINK{len(placeholders)}@@"
        label, url = match.group(1), match.group(2)
        placeholders[token] = (
            f'<link href="{html.escape(url, quote=True)}" color="#1A6A73">'
            f"{html.escape(label)}</link>"
        )
        return token

    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", protect_link, text)

    def protect_bare(match: re.Match[str]) -> str:
        token = f"@@LINK{len(placeholders)}@@"
        url = match.group(0).rstrip(".,;")
        label = "PubMed" if "pubmed.ncbi.nlm.nih.gov" in url else (
            "NICE" if "nice.org.uk" in url else "link"
        )
        placeholders[token] = (
            f'<link href="{html.escape(url, quote=True)}" color="#1A6A73">{label}</link>'
        )
        return token + match.group(0)[len(url):]

    text = re.sub(r"https?://[^\s]+", protect_bare, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(
        r"`([^`]+)`",
        r'<font name="ReviewSans" color="#5D6A76">\1</font>',
        text,
    )
    for token, value in placeholders.items():
        text = text.replace(html.escape(token), value)
    return text


def styles():
    sample = getSampleStyleSheet()
    body = ParagraphStyle(
        "ReviewBody",
        parent=sample["BodyText"],
        fontName="ReviewSans",
        fontSize=8.7,
        leading=11.4,
        textColor=INK,
        spaceAfter=5.5,
        allowWidows=0,
        allowOrphans=0,
    )
    return {
        "body": body,
        "small": ParagraphStyle(
            "ReviewSmall",
            parent=body,
            fontSize=7.2,
            leading=9.2,
            textColor=MUTED,
        ),
        "cover_label": ParagraphStyle(
            "CoverLabel",
            fontName="ReviewSans-Bold",
            fontSize=9.5,
            leading=12,
            textColor=TEAL,
            tracking=1.3,
            alignment=TA_CENTER,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            fontName="ReviewSans-Bold",
            fontSize=25,
            leading=29,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=16,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            fontName="ReviewSans",
            fontSize=13,
            leading=17,
            textColor=TEAL,
            alignment=TA_CENTER,
            spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "ReviewH1",
            parent=body,
            fontName="ReviewSans-Bold",
            fontSize=16.5,
            leading=19,
            textColor=NAVY,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "ReviewH2",
            parent=body,
            fontName="ReviewSans-Bold",
            fontSize=12.2,
            leading=15,
            textColor=TEAL,
            spaceBefore=10,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "ReviewH3",
            parent=body,
            fontName="ReviewSans-Bold",
            fontSize=9.7,
            leading=12.2,
            textColor=NAVY,
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "quote": ParagraphStyle(
            "ReviewQuote",
            parent=body,
            fontName="ReviewSans-Bold",
            fontSize=8.2,
            leading=11,
            textColor=AMBER,
            spaceAfter=0,
        ),
        "toc_title": ParagraphStyle(
            "TOCTitle",
            parent=body,
            fontName="ReviewSans-Bold",
            fontSize=18,
            leading=22,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=16,
        ),
        "toc0": ParagraphStyle(
            "TOC0",
            parent=body,
            fontName="ReviewSans-Bold",
            fontSize=9,
            leading=12,
            leftIndent=0,
            firstLineIndent=0,
            spaceBefore=3,
            textColor=NAVY,
        ),
        "toc1": ParagraphStyle(
            "TOC1",
            parent=body,
            fontName="ReviewSans",
            fontSize=7.8,
            leading=10,
            leftIndent=14,
            firstLineIndent=0,
            textColor=MUTED,
        ),
    }


class ReviewDocTemplate(SimpleDocTemplate):
    def __init__(self, filename: str, **kwargs):
        super().__init__(filename, **kwargs)
        self._bookmark_index = 0

    def beforeDocument(self):
        self._bookmark_index = 0
        return super().beforeDocument()

    def afterFlowable(self, flowable):
        level = getattr(flowable, "_toc_level", None)
        if level is None:
            return
        text = getattr(flowable, "_toc_text", "")
        key = f"toc-{self._bookmark_index}"
        self._bookmark_index += 1
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=(level > 0))
        self.notify("TOCEntry", (level, text, self.page, key))


def heading_flowable(text: str, markdown_level: int, style_map) -> Paragraph:
    display = normalize(text)
    if markdown_level <= 2:
        style = style_map["h1"]
        toc_level = 0
    elif markdown_level == 3:
        style = style_map["h2"]
        toc_level = 1
    else:
        style = style_map["h3"]
        toc_level = None
    if re.match(r"^(H\d|G\d)", display):
        toc_level = None
    paragraph = Paragraph(inline(display), style)
    paragraph._toc_level = toc_level
    paragraph._toc_text = display
    return paragraph


def parse_table(lines: list[str], style_map, available_width: float):
    rows: list[list[Paragraph]] = []
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if index == 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        style = style_map["small"]
        rows.append([Paragraph(inline(cell), style) for cell in cells])
    columns = len(rows[0])
    if columns >= 9:
        first = 0.52 * inch
        widths = [first] + [(available_width - first) / (columns - 1)] * (columns - 1)
        font_size = 5.5
        leading = 6.6
    elif columns == 4:
        widths = [0.85 * inch, 1.6 * inch, 1.2 * inch, available_width - 3.65 * inch]
        font_size = 7.1
        leading = 8.5
    else:
        widths = [available_width / columns] * columns
        font_size = 6.7
        leading = 8
    table = LongTable(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "ReviewSans-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "ReviewSans"),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("LEADING", (0, 0), (-1, -1), leading),
                ("GRID", (0, 0), (-1, -1), 0.35, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def markdown_story(markdown: str, style_map, available_width: float):
    lines = markdown.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == "## Abstract"),
        0,
    )
    lines = lines[start:]
    story = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue
        if stripped == "<!-- PAGE BREAK -->":
            story.append(PageBreak())
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            story.append(
                heading_flowable(
                    heading.group(2), len(heading.group(1)), style_map
                )
            )
            index += 1
            continue
        if stripped.startswith(">"):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            paragraph = Paragraph("<br/>".join(inline(line) for line in quote_lines), style_map["quote"])
            box = Table([[paragraph]], colWidths=[available_width])
            box.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), PALE_AMBER),
                        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#E2B34F")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.extend([box, Spacer(1, 7)])
            continue
        if stripped.startswith("|") and index + 1 < len(lines):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            story.extend(
                [parse_table(table_lines, style_map, available_width), Spacer(1, 7)]
            )
            continue
        if re.match(r"^-\s+", stripped):
            items = []
            while index < len(lines) and re.match(r"^-\s+", lines[index].strip()):
                value = re.sub(r"^-\s+", "", lines[index].strip())
                items.append(
                    ListItem(
                        Paragraph(inline(value), style_map["body"]),
                        leftIndent=12,
                    )
                )
                index += 1
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    start="circle",
                    leftIndent=18,
                    bulletFontName="ReviewSans",
                    bulletFontSize=6,
                    bulletColor=TEAL,
                    spaceAfter=5,
                )
            )
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            first_number = int(re.match(r"^(\d+)\.", stripped).group(1))
            while index < len(lines) and re.match(r"^\d+\.\s+", lines[index].strip()):
                value = re.sub(r"^\d+\.\s+", "", lines[index].strip())
                items.append(
                    ListItem(
                        Paragraph(inline(value), style_map["body"]),
                        leftIndent=14,
                    )
                )
                index += 1
            story.append(
                ListFlowable(
                    items,
                    bulletType="1",
                    start=first_number,
                    leftIndent=20,
                    bulletFontName="ReviewSans-Bold",
                    bulletFontSize=7.5,
                    bulletColor=NAVY,
                    spaceAfter=5,
                )
            )
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate == "<!-- PAGE BREAK -->"
                or re.match(r"^#{1,6}\s+", candidate)
                or candidate.startswith(">")
                or candidate.startswith("|")
                or re.match(r"^-\s+", candidate)
                or re.match(r"^\d+\.\s+", candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        story.append(Paragraph(inline(" ".join(paragraph_lines)), style_map["body"]))
    return story


def cover_story(style_map, toc: TableOfContents):
    status = Paragraph(
        "<b>PRE-HUMAN-REVIEW DRAFT</b><br/>"
        "Human Reviewer 2, formal full-text appraisal, and treating-clinician "
        "sign-off remain pending.",
        style_map["quote"],
    )
    status_box = Table([[status]], colWidths=[5.8 * inch])
    status_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_AMBER),
                ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#E2B34F")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    metadata = [
        [
            Paragraph("<b>Final search</b><br/>29 July 2026", style_map["small"]),
            Paragraph("<b>Evidence set</b><br/>92 PMIDs + 2 NICE guidelines", style_map["small"]),
            Paragraph("<b>Claim map</b><br/>66 atomic claims", style_map["small"]),
        ],
        [
            Paragraph("<b>Screened</b><br/>980 module-records", style_map["small"]),
            Paragraph("<b>Extracted</b><br/>98 source occurrences", style_map["small"]),
            Paragraph("<b>Synthesis</b><br/>Narrative; no new pooling", style_map["small"]),
        ],
    ]
    meta_table = Table(metadata, colWidths=[1.93 * inch] * 3)
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
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
            "AI-Assisted Rapid Systematic Evidence Review Draft",
            style_map["cover_subtitle"],
        ),
        Spacer(1, 0.18 * inch),
        status_box,
        Spacer(1, 0.32 * inch),
        meta_table,
        Spacer(1, 0.4 * inch),
        Paragraph(
            "Psychiatrist-facing evidence synthesis - not a diagnosis, treatment plan, "
            "or official human-gated export",
            ParagraphStyle(
                "CoverFootnote",
                parent=style_map["small"],
                alignment=TA_CENTER,
                textColor=MUTED,
            ),
        ),
        PageBreak(),
        Paragraph("Contents", style_map["toc_title"]),
        toc,
        PageBreak(),
    ]


def draw_cover_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(4)
    canvas.line(0.72 * inch, 10.45 * inch, 7.78 * inch, 10.45 * inch)
    canvas.setFont("ReviewSans", 7)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(4.25 * inch, 0.45 * inch, "Confidential working draft - clinical review required")
    canvas.restoreState()


def draw_later_page(canvas, doc):
    canvas.saveState()
    width, height = letter
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(0.72 * inch, height - 0.52 * inch, width - 0.72 * inch, height - 0.52 * inch)
    canvas.setFont("ReviewSans-Bold", 6.8)
    canvas.setFillColor(NAVY)
    canvas.drawString(0.72 * inch, height - 0.38 * inch, "ADAPTIVE-CONSTRAINT EVIDENCE REVIEW")
    canvas.setFont("ReviewSans", 6.5)
    canvas.setFillColor(AMBER)
    canvas.drawRightString(width - 0.72 * inch, height - 0.38 * inch, "HUMAN REVIEW + CLINICAL SIGN-OFF PENDING")
    canvas.setStrokeColor(RULE)
    canvas.line(0.72 * inch, 0.55 * inch, width - 0.72 * inch, 0.55 * inch)
    canvas.setFont("ReviewSans", 6.7)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.72 * inch, 0.37 * inch, "AI Reviewer-1 rapid systematic evidence-review draft")
    canvas.drawRightString(width - 0.72 * inch, 0.37 * inch, f"Page {doc.page}")
    canvas.restoreState()


def main() -> None:
    register_fonts()
    style_map = styles()
    document = ReviewDocTemplate(
        str(TARGET),
        pagesize=letter,
        rightMargin=0.72 * inch,
        leftMargin=0.72 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.68 * inch,
        title="Adaptive-Constraint and Environmental-Mismatch Model - Rapid Evidence Review",
        author="AI Reviewer-1 draft; human clinical review pending",
        subject="Rapid systematic evidence review",
    )
    toc = TableOfContents()
    story = cover_story(style_map, toc)
    story.extend(
        markdown_story(
            SOURCE.read_text(encoding="utf-8"),
            style_map,
            letter[0] - document.leftMargin - document.rightMargin,
        )
    )
    document.multiBuild(
        story,
        onFirstPage=draw_cover_page,
        onLaterPages=draw_later_page,
    )
    print(TARGET)


if __name__ == "__main__":
    main()
