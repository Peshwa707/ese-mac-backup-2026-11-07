"""Europe PMC connector — free REST API with broader coverage than PubMed.

Includes MEDLINE + PMC + preprints + some conference abstracts (ASH/ASCO are
partly indexed), and returns both PMID and DOI for cross-source dedup. Records
are verified downstream by PMID (PubMed) or DOI (Crossref), so this widens
coverage without weakening the cite-or-omit guarantee.
"""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector
from core.models import Citation, IdType, RetrievalResult

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


class EuropePmcConnector(Connector):
    source = "europepmc"

    def __init__(self, client=None, page_size: int = 200):
        super().__init__(client)
        self.page_size = min(page_size, 1000)

    async def search(
        self, query: str, structured: dict[str, Any], max_records: int = 5000
    ) -> RetrievalResult:
        """Search Europe PMC, paginating up to ``max_records`` citations."""
        try:
            citations: list[Citation] = []
            cursor = "*"
            seen_cursors: set[str] = set()
            cap = max(0, max_records)
            hit_count: int | None = None
            while len(citations) < cap:
                if cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
                page_size = min(self.page_size, cap - len(citations))
                resp = await self._request(
                    "GET", f"{_BASE}/search",
                    params={"query": query, "format": "json", "resultType": "core",
                            "pageSize": page_size, "cursorMark": cursor},
                )
                data = resp.json()
                if hit_count is None:
                    raw_hit_count = data.get("hitCount")
                    hit_count = int(raw_hit_count) if str(raw_hit_count).isdigit() else None
                results = data.get("resultList", {}).get("result", [])
                before_count = len(citations)
                for r in results:
                    if len(citations) >= cap:
                        break
                    if (c := _to_citation(r)) is not None:
                        citations.append(c)
                next_cursor = data.get("nextCursorMark")
                if len(citations) == before_count:
                    break
                if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
                    break
                if hit_count is not None and len(citations) >= hit_count:
                    break
                cursor = next_cursor
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:
            return self._unavailable(f"europepmc error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        # Existence check by id; gather_corpus does the authoritative PMID/DOI verify.
        ext_id = citation.extra_ids.get("PMID") or (citation.id if citation.id_type == IdType.PMID else "")
        q = f"EXT_ID:{ext_id}" if ext_id else (f"DOI:{citation.doi or citation.id}")
        try:
            resp = await self._request("GET", f"{_BASE}/search",
                                       params={"query": q, "format": "json", "pageSize": 1})
            return int(resp.json().get("hitCount", 0)) > 0
        except Exception:
            return False

    async def oa_locator(self, pmid: str) -> tuple[str, str] | None:
        """Return (source, id) for an OPEN-ACCESS Europe PMC record, else None.

        Only records flagged isOpenAccess=Y have a retrievable fullTextXML; paywalled
        records are not returned (no access-control circumvention).
        """
        try:
            resp = await self._request(
                "GET", f"{_BASE}/search",
                params={"query": f"EXT_ID:{pmid}", "format": "json", "resultType": "core",
                        "pageSize": 1})
            results = resp.json().get("resultList", {}).get("result", [])
            if not results:
                return None
            r = results[0]
            if str(r.get("isOpenAccess", "")).upper() != "Y":
                return None
            src, rid = r.get("source", ""), str(r.get("id", ""))
            return (src, rid) if (src and rid) else None
        except Exception:
            return None

    async def oa_fulltext_xml(self, source: str, rid: str) -> str | None:
        """Fetch the JATS full-text XML for an OA record (tables preserved). Fail-soft."""
        try:
            resp = await self._request("GET", f"{_BASE}/{source}/{rid}/fullTextXML")
            text = resp.text or ""
            return text if text.strip() else None
        except Exception:
            return None


def _to_citation(r: dict[str, Any]) -> Citation | None:
    pmid = r.get("pmid")
    doi = (r.get("doi") or "").strip()
    if pmid:
        id_type, cid = IdType.PMID, str(pmid)
    elif doi:
        id_type, cid = IdType.DOI, doi
    else:
        cid = r.get("id")
        if not cid:
            return None
        id_type = IdType.URL
    year = r.get("pubYear")
    src = r.get("source", "")
    return Citation(
        id_type=id_type, id=cid, source="europepmc",
        title=r.get("title", ""),
        url=f"https://europepmc.org/article/{src}/{r.get('id', '')}" if src else "",
        snippet=r.get("abstractText", "") or "",
        study_type=r.get("pubType") or None,
        year=int(year) if str(year).isdigit() else None,
        doi=doi,
        extra_ids={"PMID": str(pmid)} if (pmid and id_type != IdType.PMID) else {},
    )
