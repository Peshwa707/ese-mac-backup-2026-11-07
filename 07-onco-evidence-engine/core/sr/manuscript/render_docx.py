"""Serialize Components to a python-docx Document (editable Word format).

SVG figures can't be embedded natively by python-docx; the figure is referenced
by its filename in figures/ with a caption, rather than rasterized — honest and
lossless (the SVG ships alongside in the project folder).
"""

from __future__ import annotations

from docx import Document
from docx.shared import Pt, RGBColor

from core.sr.manuscript.model import (
    BulletList,
    CodeBlock,
    Component,
    Figure,
    Heading,
    Note,
    Paragraph,
    Table,
)

_NOTE_LABEL = {"info": "NOTE", "warning": "CAUTION", "todo": "TO COMPLETE"}


def _add_block(doc: Document, b) -> None:
    if isinstance(b, Heading):
        doc.add_heading(b.text, level=max(1, min(b.level, 4)))
    elif isinstance(b, Paragraph):
        doc.add_paragraph(b.text)
    elif isinstance(b, BulletList):
        for it in b.items:
            doc.add_paragraph(str(it), style="List Bullet")
    elif isinstance(b, Table):
        if b.caption:
            cap = doc.add_paragraph()
            run = cap.add_run(b.caption)
            run.italic = True
        ncols = len(b.headers)
        table = doc.add_table(rows=1, cols=ncols)
        table.style = "Table Grid"
        for i, h in enumerate(b.headers):
            run = table.rows[0].cells[i].paragraphs[0].add_run(str(h))
            run.bold = True
        for row in b.rows:
            cells = table.add_row().cells
            for i in range(ncols):
                cells[i].text = str(row[i]) if i < len(row) else ""
    elif isinstance(b, Figure):
        import os
        name = os.path.basename(b.svg_path) if b.svg_path else "figure"
        p = doc.add_paragraph()
        run = p.add_run(f"[Figure: {b.caption or name} — see figures/{name}]")
        run.italic = True
    elif isinstance(b, Note):
        label = _NOTE_LABEL.get(b.kind, "NOTE")
        p = doc.add_paragraph()
        lab = p.add_run(f"{label}: ")
        lab.bold = True
        lab.font.color.rgb = RGBColor(0x8A, 0x4A, 0x00) if b.kind != "info" else RGBColor(0x23, 0x39, 0x5D)
        p.add_run(b.text).italic = True
    elif isinstance(b, CodeBlock):
        p = doc.add_paragraph()
        run = p.add_run(b.text)
        run.font.name = "Courier New"
        run.font.size = Pt(9)


def _add_component(doc: Document, c: Component, *, title_level: int = 0) -> None:
    doc.add_heading(c.title, level=title_level)
    for b in c.blocks:
        _add_block(doc, b)


def component_to_docx(c: Component) -> Document:
    doc = Document()
    _add_component(doc, c, title_level=0)
    return doc


def components_to_docx(components: list[Component]) -> Document:
    doc = Document()
    for i, c in enumerate(components):
        if i:
            doc.add_page_break()
        _add_component(doc, c, title_level=1)
    return doc
