"""Deterministic PubMed boolean query builder — single source for doc + search YAML."""

from __future__ import annotations

from core.sr.protocol.spec import ConceptBlock


def _term(t: str) -> str:
    t = t.strip()
    # Allow pre-tagged terms ("...[tiab]", "...[Mesh]") through unchanged.
    return t if t.endswith("]") else f"{t}[tiab]"


def build_query(blocks: list[ConceptBlock], date_from: str = "", date_to: str = "") -> str:
    """AND of per-block OR-groups; MeSH headings + [tiab] terms; optional date filter."""
    parts: list[str] = []
    for b in blocks:
        ors = [f'"{m}"[Mesh]' for m in b.mesh] + [_term(t) for t in b.terms]
        if ors:
            parts.append("( " + " OR ".join(ors) + " )")
    query = " AND ".join(parts)
    if date_from or date_to:
        df = date_from or "1800/01/01"
        dt = date_to or "3000/12/31"
        query += f' AND ("{df}"[dp] : "{dt}"[dp])'
    return query
