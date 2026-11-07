"""ID verification, cite-or-omit, and quote-anchoring.

Every citation must resolve against its source's live API or it is rejected — no
invented identifiers survive (spec §4.2). Findings must then carry >=1 verified
citation (cite-or-omit) and an extracted quote that actually appears in a
retrieved snippet (quote-anchoring).
"""

from __future__ import annotations

import asyncio
import re

from core.connectors.base import Connector
from core.models import Citation, Finding

_WS_RE = re.compile(r"\s+")


async def verify_citations(
    connectors: list[Connector], citations: list[Citation]
) -> tuple[list[Citation], list[dict]]:
    """Resolve each citation; return (verified, rejected_records)."""
    by_source = {c.source: c for c in connectors}

    async def _check(c: Citation) -> tuple[Citation, bool]:
        connector = by_source.get(c.source)
        if connector is None:
            return c, False
        try:
            ok = await connector.verify(c)
        except Exception:
            ok = False
        return c, ok

    checked = await asyncio.gather(*(_check(c) for c in citations))
    verified: list[Citation] = []
    rejected: list[dict] = []
    for c, ok in checked:
        c.verified = ok
        if ok:
            verified.append(c)
        else:
            rejected.append({"key": c.key, "source": c.source, "reason": "id_unresolved"})
    return verified, rejected


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", s).strip().lower()


def quote_is_anchored(quote: str, citations: list[Citation], min_len: int = 12) -> bool:
    """True if the quote is a substring of one of the findings' citation snippets."""
    if not quote or len(quote.strip()) < min_len:
        return False
    nq = _norm(quote)
    return any(nq in _norm(c.snippet) for c in citations if c.snippet)


def apply_cite_or_omit(
    findings: list[Finding], verified_keys: set[str]
) -> tuple[list[Finding], list[dict]]:
    """Keep only findings backed by >=1 verified citation AND an anchored quote.

    Quote-anchoring is a GATE, not a flag: a finding whose quote is empty,
    shorter than the minimum length, or not a verbatim substring of one of its
    verified citation snippets is DROPPED (reason ``"quote_not_anchored"``) so
    the pipeline can log it as a rejection. This closes the hole where an
    unanchored — i.e. unverifiable — quote could still ship a clinical claim.

    Returns (kept_findings, dropped_records). Kept findings are re-indexed; their
    ``conflicts_with`` references are remapped to the new indices.
    """
    old_to_new: dict[int, int] = {}
    kept: list[Finding] = []
    dropped: list[dict] = []

    for old_idx, f in enumerate(findings):
        verified = [c for c in f.citations if c.key in verified_keys]
        if not verified:
            dropped.append({"statement": f.statement, "reason": "no_verified_citation"})
            continue
        if not quote_is_anchored(f.quote, verified):
            dropped.append({"statement": f.statement, "reason": "quote_not_anchored"})
            continue
        f.citations = verified
        for c in f.citations:
            c.verified = True
        old_to_new[old_idx] = len(kept)
        kept.append(f)

    for f in kept:
        f.conflicts_with = [old_to_new[i] for i in f.conflicts_with if i in old_to_new]
    return kept, dropped
