"""Scopus Search API connector (Elsevier).

Contract (verified against the Scopus Search API WADL + elsapy + pybliometrics, 2026):
  base   : https://api.elsevier.com/content/search/scopus
  auth   : header  X-ELS-APIKey: <key>   (+ optional  X-ELS-Insttoken: <token>)
  params : query (e.g. TITLE-ABS-KEY(...)), view (STANDARD|COMPLETE), count (<=25),
           start (offset), httpAccept=application/json
  resp   : {"search-results": {"opensearch:totalResults": "N",
             "entry": [{"dc:title","prism:doi","pubmed-id","prism:coverDate","eid",...}]}}
           An empty result set returns a single entry carrying an "error" field.

Access: an Elsevier API key is free to request, but FULL search results require an
institutional subscription — typically an **institutional token** (X-ELS-Insttoken) or
running on the subscribing institution's network. Without a key the connector degrades
to `unavailable`; with a non-subscriber key it may return the limited BASIC view.

Keys are read from the providers.env secrets store: SCOPUS_API_KEY (required) and
SCOPUS_INSTTOKEN (optional). PMID/DOI records verify downstream (PubMed/Crossref);
EID-only records stay unverified and are dropped by cite-or-omit (not attested).
"""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector
from core.connectors.credentials import search_credential
from core.models import Citation, IdType, RetrievalResult

_BASE = "https://api.elsevier.com/content/search/scopus"
_MAX_COUNT = 25   # API cap per page for the search resource


class ScopusConnector(Connector):
    source = "scopus"

    def __init__(self, client=None, page_size: int = 25, view: str = "STANDARD",
                 api_key: str | None = None, insttoken: str | None = None,
                 max_records: int = 1000):
        super().__init__(client)
        self.page_size = max(1, min(page_size, _MAX_COUNT))
        self.view = view
        self._api_key = api_key
        self._insttoken = insttoken
        self.max_records = max_records

    async def search(self, query: str, structured: dict[str, Any],
                     max_records: int | None = None) -> RetrievalResult:
        try:
            # Credential lookup is inside the try: search_credential() fails CLOSED on an
            # insecure providers.env (symlink/perms/owner), and gather_corpus does not
            # wrap per-source search — so a raise here must degrade only Scopus, not abort
            # the whole multi-source review.
            key = self._api_key or search_credential("SCOPUS_API_KEY")
            if not key:
                return self._unavailable(
                    "no SCOPUS_API_KEY configured (Elsevier Scopus API key)", query)
            insttoken = self._insttoken or search_credential("SCOPUS_INSTTOKEN")
            headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
            if insttoken:
                headers["X-ELS-Insttoken"] = insttoken

            cap = max(0, max_records if max_records is not None else self.max_records)
            citations: list[Citation] = []
            start = 0
            total: int | None = None
            count = self.page_size   # constant per page; Scopus paginates by `start` offset
            while len(citations) < cap:
                resp = await self._request(
                    "GET", _BASE,
                    params={"query": query, "count": count, "start": start, "view": self.view},
                    headers=headers,
                )
                sr = (resp.json() or {}).get("search-results") or {}
                if total is None:
                    raw = sr.get("opensearch:totalResults")
                    total = int(raw) if str(raw).isdigit() else None
                entries = sr.get("entry") or []
                # Scopus signals "no results" with a single entry carrying an error.
                is_error = len(entries) == 1 and bool(entries[0].get("error"))
                if not entries or is_error:
                    # An empty/error page while `total` still advertises more records is a
                    # truncated search, NOT a clean end — fail (→ unavailable) rather than
                    # silently returning an incomplete corpus as if complete.
                    if total is not None and len(citations) < total:
                        raise RuntimeError(
                            f"scopus returned an empty/error page at start={start} but "
                            f"total={total} > fetched={len(citations)} (incomplete search)")
                    break
                before = len(citations)
                for e in entries:
                    if len(citations) >= cap:
                        break
                    if (c := _to_citation(e)) is not None:
                        citations.append(c)
                if len(citations) == before:
                    break   # no new parseable records — stop advancing
                if total is not None and len(citations) >= total:
                    break
                start += count
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:
            # Fail closed: an incomplete Scopus result (mid-pagination error, or the
            # 5000-offset ceiling) degrades the whole source to unavailable rather than
            # silently returning a partial corpus as if complete — completeness is a
            # validity claim in a systematic review. (The ceiling is unreachable at the
            # default max_records=1000.)
            return self._unavailable(f"scopus error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        return True   # gather_corpus does the authoritative PMID/DOI verification


def _to_citation(e: dict[str, Any]) -> Citation | None:
    pmid = str(e.get("pubmed-id") or "").strip()
    doi = str(e.get("prism:doi") or "").strip()
    eid = str(e.get("eid") or e.get("dc:identifier") or "").strip()
    if pmid:
        id_type, cid = IdType.PMID, pmid
    elif doi:
        id_type, cid = IdType.DOI, doi
    elif eid:
        # No PMID/DOI to verify against — stays unverified and cite-or-omit drops it
        # (not attested via the imported-export channel).
        id_type, cid = IdType.URL, eid
    else:
        return None
    date = str(e.get("prism:coverDate") or "")
    year = date[:4] if date[:4].isdigit() else ""
    extra: dict[str, str] = {}
    if pmid and id_type != IdType.PMID:
        extra["PMID"] = pmid
    if eid:
        extra["EID"] = eid
    return Citation(
        id_type=id_type, id=cid, source="scopus",
        title=str(e.get("dc:title") or ""),
        year=int(year) if year else None,
        doi=doi,
        extra_ids=extra,
    )
