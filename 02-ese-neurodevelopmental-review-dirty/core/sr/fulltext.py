"""Resolve full text for included studies, honestly tracking source + coverage.

Priority: a user-supplied PDF (institutional access — the realistic path for
paywalled pivotal trials) → PMC open access → Unpaywall. Studies with none stay
abstract-level and are reported as such; no full text is ever fabricated.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.connectors.pmc import PmcConnector
from core.connectors.unpaywall import UnpaywallConnector
from core.paths import ENGINE_BASE, path_env

INBOX = path_env("EVIDENCE_FULLTEXT_INBOX", default=ENGINE_BASE / "_fulltext_inbox")
_MIN_CHARS = 500  # below this we treat extraction as failed (e.g. scanned image PDF)

_README = """# Full-text inbox

Drop full-text PDFs here to enrich extraction for paywalled studies (pivotal
trials are often not open-access, so this is how their hazard ratios and other
full-text-only outcomes get into the review).

- Name each file by its PubMed ID: `<PMID>.pdf`  (e.g. `37272512.pdf`).
- Only add PDFs you are licensed to use. They stay on your machine.
- Re-run the full-text extraction afterwards; newly-added PDFs are picked up
  automatically, and every extracted value still carries its verbatim source quote.

Included-study PMIDs are listed in the run's `includes.json`.
"""


@dataclass
class FullText:
    pmid: str
    available: bool
    source: str   # user_pdf | pmc | unpaywall | none
    text: str = ""
    chars: int = 0


def ensure_inbox(inbox: Path = INBOX) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    readme = inbox / "README.md"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
    return inbox


def pdf_to_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    except Exception:
        return ""


async def _resolve_one(pmid: str, doi: Optional[str], inbox: Path,
                       pmc: PmcConnector, uw: UnpaywallConnector) -> FullText:
    pdf = inbox / f"{pmid}.pdf"
    if pdf.exists():
        t = pdf_to_text(pdf)
        if len(t) >= _MIN_CHARS:
            return FullText(pmid, True, "user_pdf", t, len(t))
    t = await pmc.fulltext(pmid)
    if t and len(t) >= _MIN_CHARS:
        return FullText(pmid, True, "pmc", t, len(t))
    if doi:
        t = await uw.fulltext(doi)
        if t and len(t) >= _MIN_CHARS:
            return FullText(pmid, True, "unpaywall", t, len(t))
    return FullText(pmid, False, "none")


async def resolve_many(
    pmids: list[str], inbox: Path = INBOX, *, concurrency: int = 4
) -> list[FullText]:
    """Resolve full text for each PMID. DOIs are looked up via PMC/esummary."""
    ensure_inbox(inbox)
    pmc, uw = PmcConnector(), UnpaywallConnector()
    sem = asyncio.Semaphore(concurrency)

    async def _one(pmid: str) -> FullText:
        async with sem:
            doi = await pmc.doi_for(pmid)
            return await _resolve_one(pmid, doi, inbox, pmc, uw)

    try:
        return list(await asyncio.gather(*(_one(p) for p in pmids)))
    finally:
        await pmc.aclose()
        await uw.aclose()


def coverage_summary(results: list[FullText]) -> dict:
    by_source: dict[str, int] = {}
    for r in results:
        by_source[r.source] = by_source.get(r.source, 0) + 1
    have = sum(1 for r in results if r.available)
    return {"total": len(results), "with_fulltext": have,
            "abstract_only": len(results) - have, "by_source": by_source}
