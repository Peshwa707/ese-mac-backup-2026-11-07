"""Serialize a Component to standalone HTML (for the Chrome→PDF path)."""

from __future__ import annotations

import html
from pathlib import Path

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

_CSS = """
body { font: 13px/1.6 -apple-system, Georgia, 'Times New Roman', serif; color:#1a1a1a;
       max-width: 820px; margin: 36px auto; padding: 0 24px; }
h1 { font-size: 22px; border-bottom: 2px solid #34507a; padding-bottom: 6px; }
h2 { font-size: 17px; color:#23395d; margin-top: 22px; }
h3 { font-size: 14px; color:#3a3a3a; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }
th, td { border: 1px solid #cfd8e6; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #eef2f8; }
.note { border-left: 4px solid #34507a; background:#f4f7fb; padding: 8px 14px; margin: 12px 0; border-radius: 0 6px 6px 0; }
.note.warning { border-color:#b8860b; background:#fff8e6; }
.note.todo { border-color:#7a4ea0; background:#f6f0fb; }
figure { margin: 16px 0; text-align: center; }
figcaption { font-size: 11px; color:#5a6b85; font-style: italic; }
code, pre { font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
pre { background:#f6f8fa; padding: 10px 12px; border-radius: 6px; overflow-x:auto; white-space: pre-wrap; }
"""


def _esc(s: str) -> str:
    return html.escape(str(s))


def _block_html(b, figures_dir: Path | None) -> str:
    if isinstance(b, Heading):
        lvl = max(1, min(b.level, 4))
        return f"<h{lvl}>{_esc(b.text)}</h{lvl}>"
    if isinstance(b, Paragraph):
        return f"<p>{_esc(b.text)}</p>"
    if isinstance(b, BulletList):
        return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in b.items) + "</ul>"
    if isinstance(b, Table):
        head = "<tr>" + "".join(f"<th>{_esc(h)}</th>" for h in b.headers) + "</tr>"
        body = "".join(
            "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>" for row in b.rows
        )
        cap = f"<figcaption>{_esc(b.caption)}</figcaption>" if b.caption else ""
        return f"<table>{head}{body}</table>{cap}"
    if isinstance(b, Figure):
        inner = ""
        if b.svg_path:
            p = Path(b.svg_path)
            if not p.is_absolute() and figures_dir:
                p = figures_dir / p.name
            if p.suffix.lower() == ".svg" and p.exists():
                inner = p.read_text(encoding="utf-8")          # inline SVG
            elif p.exists():
                inner = f"<img src='file://{p.resolve()}' style='max-width:100%'/>"  # PNG/JPG
        cap = f"<figcaption>{_esc(b.caption)}</figcaption>" if b.caption else ""
        return f"<figure>{inner}{cap}</figure>"
    if isinstance(b, Note):
        return f'<div class="note {_esc(b.kind)}">{_esc(b.text)}</div>'
    if isinstance(b, CodeBlock):
        return f"<pre><code>{_esc(b.text)}</code></pre>"
    return ""


def _body(components: list[Component], figures_dir: Path | None) -> str:
    out = []
    for c in components:
        out.append(f"<h1>{_esc(c.title)}</h1>")
        for b in c.blocks:
            out.append(_block_html(b, figures_dir))
    return "\n".join(out)


def component_to_html(c: Component, figures_dir: Path | None = None) -> str:
    return _document([c], figures_dir)


def components_to_html(components: list[Component], figures_dir: Path | None = None) -> str:
    return _document(components, figures_dir)


def _document(components: list[Component], figures_dir: Path | None) -> str:
    title = components[0].title if len(components) == 1 else "Systematic Review Manuscript"
    return (
        f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head>"
        f"<body>{_body(components, figures_dir)}</body></html>"
    )
