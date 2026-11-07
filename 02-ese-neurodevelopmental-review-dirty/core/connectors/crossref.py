"""DOI verification via Crossref — the DOI analogue of PMID round-trip checking.

Lets records that carry only a DOI (Europe PMC, imported exports) be live-verified
the same way PMIDs are, so cite-or-omit holds across sources. A DOI resolves iff
Crossref returns a work whose DOI matches.
"""

from __future__ import annotations

import asyncio

import httpx

_API = "https://api.crossref.org/works"
_HEADERS = {"User-Agent": "onco-evidence-engine/1.0 (mailto:research@example.org)"}


async def verify_doi(doi: str, client: httpx.AsyncClient | None = None) -> bool:
    if not doi:
        return False
    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0, headers=_HEADERS)
    try:
        resp = await client.get(f"{_API}/{doi}")
        if resp.status_code != 200:
            return False
        got = (resp.json().get("message", {}) or {}).get("DOI", "")
        return got.strip().lower() == doi.strip().lower()
    except Exception:
        return False
    finally:
        if own:
            await client.aclose()


async def verify_dois(dois: list[str], *, concurrency: int = 5) -> set[str]:
    """Resolve many DOIs concurrently (Crossref has no bulk endpoint)."""
    uniq = [d for d in dict.fromkeys(dois) if d]
    if not uniq:
        return set()
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
        async def _one(d: str) -> tuple[str, bool]:
            async with sem:
                return d, await verify_doi(d, client)
        results = await asyncio.gather(*(_one(d) for d in uniq))
    return {d for d, ok in results if ok}
