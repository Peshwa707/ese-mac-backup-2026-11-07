"""Honesty audit for a systematic-review run.

Two checks that make the run defensible rather than merely plausible:
1. **Live PMID resolution** — every included study's PMID must still resolve
   against PubMed (cite-or-omit, re-checked at audit time).
2. **Quote-anchoring** — every extracted non-NR, non-ESTIMATED value must trace
   to its source abstract: either its supporting quote, or the value itself,
   appears in the abstract (after normalizing whitespace and the middle-dot /
   en-dash conventions that NEJM/Lancet abstracts use for decimals and ranges).

Identity/metadata fields (year, first_author) come from PubMed record metadata,
not the abstract, so they are reported separately rather than counted as
anchoring failures. Values the extractor marked ESTIMATED are derivations, not
verbatim claims, and are surfaced for human review, not auto-failed.

The audit never mutates the run; it only reports. Honest output beats a clean
number — unresolved PMIDs and unanchored substantive values are listed, not hidden.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from core.connectors.pubmed import PubMedConnector
from core.sr.models import ExtractedStudy

_WS = re.compile(r"\s+")
# Fields sourced from record metadata, not the abstract text.
METADATA_FIELDS = {"year", "first_author"}
_MIN_QUOTE = 12


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "")).strip().lower()


def _norm_punct(s: str) -> str:
    # NEJM/Lancet abstracts render decimals with a middle dot (12·5) and ranges
    # with en/em dashes; normalize so a quote copied as 12.5 / 12-5 still matches.
    return _norm(s).replace("·", ".").replace("–", "-").replace("—", "-").replace(" ", " ")


def _value_grounded(value: str, quote: str, abstract: str) -> bool:
    ab, ab2 = _norm(abstract), _norm_punct(abstract)
    q, q2 = _norm(quote), _norm_punct(quote)
    if q and len(q) >= _MIN_QUOTE and (q in ab or q2 in ab2):
        return True
    # Compound values like "3.48 months (overall); 9.2 months" anchor if the
    # leading numeric token is present in the abstract.
    v = _norm_punct(value)
    if v and v in ab2:
        return True
    lead = v.split(";")[0].split("(")[0].strip()
    return bool(lead) and len(lead) >= 3 and lead in ab2


def audit_quote_anchoring(
    abstracts: dict[str, str], extractions: Iterable[ExtractedStudy]
) -> dict:
    """Classify every extracted value. Returns a structured, honest report."""
    total = anchored = unanchored = estimated = nr = metadata = 0
    unanchored_items: list[dict] = []
    for es in extractions:
        ab = abstracts.get(es.pmid, "")
        for v in es.values:
            val = (v.value or "").strip()
            if v.flag == "NR" or val.upper() == "NR":
                nr += 1
                continue
            if v.flag == "ESTIMATED" or val.upper().startswith("ESTIMATED"):
                estimated += 1
                continue
            if v.field in METADATA_FIELDS:
                metadata += 1
                continue
            total += 1
            if _value_grounded(val, v.quote, ab):
                anchored += 1
            else:
                unanchored += 1
                unanchored_items.append(
                    {"pmid": es.pmid, "field": v.field, "value": val[:40], "quote": (v.quote or "")[:60]}
                )
    rate = (anchored / total) if total else 1.0
    return {
        "substantive_values": total,
        "anchored": anchored,
        "unanchored": unanchored,
        "anchored_rate": round(rate, 4),
        "estimated_for_human_review": estimated,
        "nr": nr,
        "metadata_values": metadata,
        "unanchored_items": unanchored_items,
    }


async def audit_pmid_resolution(
    pmids: list[str], connector: Optional[PubMedConnector] = None
) -> dict:
    """Re-resolve every PMID against the live API. Returns resolved/unresolved."""
    own = connector is None
    connector = connector or PubMedConnector()
    try:
        resolved = await connector.verify_bulk(pmids)
    finally:
        if own:
            await connector.aclose()
    unresolved = [p for p in pmids if p not in resolved]
    return {"checked": len(pmids), "resolved": len(resolved), "unresolved": unresolved}
