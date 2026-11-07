"""Serialize a Component to GitHub-flavored Markdown (the editable source)."""

from __future__ import annotations

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

_NOTE_PREFIX = {"info": "ℹ️", "warning": "⚠️", "todo": "📝"}


def _esc_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _block_md(b) -> str:
    if isinstance(b, Heading):
        return f"{'#' * max(1, min(b.level, 6))} {b.text}"
    if isinstance(b, Paragraph):
        return b.text
    if isinstance(b, BulletList):
        return "\n".join(f"- {it}" for it in b.items)
    if isinstance(b, Table):
        out = []
        if b.caption:
            out.append(f"*{b.caption}*\n")
        head = "| " + " | ".join(_esc_cell(h) for h in b.headers) + " |"
        sep = "| " + " | ".join("---" for _ in b.headers) + " |"
        out.append(head)
        out.append(sep)
        for row in b.rows:
            out.append("| " + " | ".join(_esc_cell(c) for c in row) + " |")
        return "\n".join(out)
    if isinstance(b, Figure):
        import os
        name = os.path.basename(b.svg_path) if b.svg_path else ""
        img = f"![{b.caption}](../figures/{name})" if name else ""
        cap = f"\n*{b.caption}*" if b.caption else ""
        return f"{img}{cap}".strip()
    if isinstance(b, Note):
        prefix = _NOTE_PREFIX.get(b.kind, "ℹ️")
        body = b.text.replace("\n", "\n> ")
        return f"> {prefix} {body}"
    if isinstance(b, CodeBlock):
        return f"```{b.lang}\n{b.text}\n```"
    return ""


def component_to_md(c: Component) -> str:
    parts = [f"# {c.title}", ""]
    for b in c.blocks:
        parts.append(_block_md(b))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def components_to_md(components: list[Component]) -> str:
    """Combined full manuscript (components joined with rules)."""
    return "\n\n---\n\n".join(component_to_md(c) for c in components)
