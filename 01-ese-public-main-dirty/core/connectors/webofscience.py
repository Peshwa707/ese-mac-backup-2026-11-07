"""Web of Science Starter API connector (Clarivate).

Contract (verified against the official clarivate/wosstarter_python_client, 2026):
  base   : https://api.clarivate.com/apis/wos-starter/v1
  search : GET /documents
  auth   : header  X-ApiKey: <key>
  params : q (advanced query; field tags TS= topic, TI=, DO=, PMID=, PY=),
           db (default WOS; MEDLINE also available), limit (1-50), page
  resp   : {"metadata": {"total","page","limit"}, "hits": [Document]}
           Document.identifiers = {"doi","pmid","issn",...}; .source = {"sourceTitle","publishYear"}

Access tiers: a FREE Starter plan (50 requests/day) is available to anyone; a
subscriber key raises the daily limit and adds times-cited. The key is read from
the providers.env secrets store (env var WOS_STARTER_KEY). With no key the
connector degrades to an `unavailable` result — the run continues on other sources.

Records with a PMID or DOI verify downstream (PubMed/Crossref). A WoS-UID-only
record has no independent id to verify, so it stays UNVERIFIED and cite-or-omit
drops it (it is NOT attested via the imported-export channel) — it only survives by
deduping onto a PMID/DOI-bearing copy from another source via the WOS extra_id.
"""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector
from core.connectors.credentials import search_credential
from core.models import Citation, IdType, RetrievalResult

_BASE = "https://api.clarivate.com/apis/wos-starter/v1"
_MAX_LIMIT = 50   # API hard cap per page


class WebOfScienceConnector(Connector):
    source = "webofscience"

    def __init__(self, client=None, page_size: int = 50, db: str = "WOS",
                 api_key: str | None = None, max_records: int = 1000):
        super().__init__(client)
        self.page_size = max(1, min(page_size, _MAX_LIMIT))
        self.db = db
        self._api_key = api_key                 # optional injection (tests)
        self.max_records = max_records

    async def search(self, query: str, structured: dict[str, Any],
                     max_records: int | None = None) -> RetrievalResult:
        try:
            # Credential lookup inside the try: search_credential() fails CLOSED on an
            # insecure providers.env, and gather_corpus does not wrap per-source search —
            # a raise here must degrade only WoS, not abort the whole multi-source review.
            key = self._api_key or search_credential("WOS_STARTER_KEY")
            if not key:
                return self._unavailable(
                    "no WOS_STARTER_KEY configured (Web of Science Starter API key)", query)
            cap = max(0, max_records if max_records is not None else self.max_records)
            citations: list[Citation] = []
            page = 1
            total: int | None = None
            # The request limit MUST stay constant across pages: WoS offsets are
            # (page-1)*limit, so shrinking limit on later pages corrupts the offset
            # and silently drops the tail of the result set.
            req_limit = min(self.page_size, cap) if cap else self.page_size
            while len(citations) < cap:
                resp = await self._request(
                    "GET", f"{_BASE}/documents",
                    params={"q": query, "db": self.db, "limit": req_limit, "page": page},
                    headers={"X-ApiKey": key},
                )
                data = resp.json()
                if total is None:
                    raw = (data.get("metadata") or {}).get("total")
                    total = int(raw) if str(raw).isdigit() else None
                hits = data.get("hits") or []
                if not hits:
                    # Empty page while `total` still advertises more = truncated search;
                    # fail (→ unavailable) rather than return an incomplete corpus as complete.
                    if total is not None and len(citations) < total:
                        raise RuntimeError(
                            f"webofscience returned an empty page at page={page} but "
                            f"total={total} > fetched={len(citations)} (incomplete search)")
                    break
                before = len(citations)
                for h in hits:
                    if len(citations) >= cap:
                        break
                    if (c := _to_citation(h)) is not None:
                        citations.append(c)
                if len(citations) == before:
                    break
                if total is not None and len(citations) >= total:
                    break
                page += 1
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:
            return self._unavailable(f"webofscience error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        # gather_corpus does the authoritative PMID/DOI verification; UID-only
        # records are attested on provenance.
        return True


def _to_citation(h: dict[str, Any]) -> Citation | None:
    ids = h.get("identifiers") or {}
    pmid = str(ids.get("pmid") or "").strip()
    doi = str(ids.get("doi") or "").strip()
    uid = str(h.get("uid") or "").strip()
    if pmid:
        id_type, cid = IdType.PMID, pmid
    elif doi:
        id_type, cid = IdType.DOI, doi
    elif uid:
        # No PMID/DOI to verify against. We deliberately do NOT attest on WoS
        # provenance (that channel means "human-exported from a licensed DB file"):
        # a UID-only record has no independent id to check, so it stays unverified
        # and cite-or-omit drops it like any other unverifiable record. It can still
        # dedup (by title+year) against a PMID/DOI-bearing copy from another source
        # via the WOS extra_id.
        id_type, cid = IdType.URL, uid
    else:
        return None
    src = h.get("source") or {}
    year = src.get("publishYear", src.get("publish_year"))
    extra: dict[str, str] = {}
    if pmid and id_type != IdType.PMID:
        extra["PMID"] = pmid
    if uid:
        extra["WOS"] = uid
    return Citation(
        id_type=id_type, id=cid, source="webofscience",
        title=str(h.get("title") or ""),
        year=int(year) if str(year).isdigit() else None,
        doi=doi,
        extra_ids=extra,
    )
