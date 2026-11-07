"""PubMed connector via NCBI E-utilities (esearch/efetch/esummary)."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from core.connectors.base import Connector
from core.models import Citation, IdType, RetrievalResult

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_EFETCH_CHUNK = 200  # ids per efetch request (keeps URL length safe)


class PubMedConnector(Connector):
    source = "pubmed"

    def __init__(self, client=None, retmax: int = 20):
        super().__init__(client)
        self.retmax = retmax
        self._api_key = os.environ.get("NCBI_API_KEY")
        # NCBI E-utilities policy: every request SHOULD identify the caller via
        # `tool` and `email`. Without them (and without an api_key) NCBI throttles
        # at 3 req/s and may block. Configurable via env.
        self._tool = os.environ.get("NCBI_TOOL", "onco-evidence-engine")
        self._email = os.environ.get("NCBI_EMAIL", "")

    def _params(self, **kw: Any) -> dict[str, Any]:
        p = {"db": "pubmed", "tool": self._tool, **kw}
        if self._email:
            p["email"] = self._email
        if self._api_key:
            p["api_key"] = self._api_key
        return p

    async def search(self, query: str, structured: dict[str, Any]) -> RetrievalResult:
        try:
            esearch = await self._request(
                "GET",
                f"{_BASE}/esearch.fcgi",
                params=self._params(term=query, retmax=self.retmax, retmode="json"),
            )
            ids = esearch.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return RetrievalResult(source=self.source, query=query, citations=[])
            # Fetch abstracts in chunks: a single efetch URL can't hold thousands
            # of ids, and chunking keeps each request well under URL limits while
            # staying sequential to respect NCBI's unauthenticated rate limit.
            citations: list[Citation] = []
            for i in range(0, len(ids), _EFETCH_CHUNK):
                batch = ids[i : i + _EFETCH_CHUNK]
                efetch = await self._request(
                    "GET",
                    f"{_BASE}/efetch.fcgi",
                    params=self._params(id=",".join(batch), rettype="abstract", retmode="xml"),
                )
                citations.extend(_parse_efetch(efetch.text))
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:  # fail soft
            return self._unavailable(f"pubmed error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        if citation.id_type != IdType.PMID:
            return False
        try:
            resp = await self._request(
                "GET",
                f"{_BASE}/esummary.fcgi",
                params=self._params(id=citation.id, retmode="json"),
            )
            result = resp.json().get("result", {})
            entry = result.get(citation.id)
            return bool(entry) and "error" not in entry
        except Exception:
            return False

    async def _elink(self, pmid: str, linkname: str) -> list[str]:
        try:
            resp = await self._request(
                "GET", f"{_BASE}/elink.fcgi",
                params=self._params(dbfrom="pubmed", db="pubmed", id=pmid,
                                    retmode="json", linkname=linkname),
            )
            out: list[str] = []
            for ls in resp.json().get("linksets", []):
                for ldb in ls.get("linksetdbs", []):
                    if ldb.get("linkname") == linkname:
                        out.extend(ldb.get("links") or [])
            return out
        except Exception:
            return []

    async def fetch_ncts(self, pmids: list[str]) -> dict[str, str]:
        """Return {pmid: 'NCT...,NCT...'} for the given PMIDs (efetch + DataBank parse).

        Used to backfill trial registrations onto an existing run's included set.
        """
        out: dict[str, str] = {}
        unique = [p for p in dict.fromkeys(pmids) if p]
        for i in range(0, len(unique), _EFETCH_CHUNK):
            batch = unique[i : i + _EFETCH_CHUNK]
            try:
                resp = await self._request(
                    "GET", f"{_BASE}/efetch.fcgi",
                    params=self._params(id=",".join(batch), rettype="abstract", retmode="xml"),
                )
            except Exception:
                continue
            for c in _parse_efetch(resp.text):
                nct = (c.extra_ids or {}).get("NCT")
                if nct:
                    out[c.id] = nct
        return out

    async def references(self, pmid: str) -> list[str]:
        """Backward citation mining: PMIDs this article references."""
        return await self._elink(pmid, "pubmed_pubmed_refs")

    async def cited_by(self, pmid: str) -> list[str]:
        """Forward citation mining: PMIDs that cite this article."""
        return await self._elink(pmid, "pubmed_pubmed_citedin")

    async def verify_bulk(self, pmids: list[str], chunk: int = 180) -> set[str]:
        """Resolve many PMIDs in batched esummary calls (one round-trip per chunk).

        Per-citation ``verify`` fired concurrently over a large corpus would trip
        NCBI's 3 req/s unauthenticated limit and falsely reject valid PMIDs. This
        batches ids so a 120-record corpus is one real round-trip, not 120. A PMID
        is "resolved" iff esummary returns an entry for it without an ``error``.
        """
        resolved: set[str] = set()
        unique = [p for p in dict.fromkeys(pmids) if p]
        for i in range(0, len(unique), chunk):
            batch = unique[i : i + chunk]
            try:
                resp = await self._request(
                    "GET",
                    f"{_BASE}/esummary.fcgi",
                    params=self._params(id=",".join(batch), retmode="json"),
                )
                result = resp.json().get("result", {})
            except Exception:
                continue  # leave this chunk unresolved rather than guess
            for pmid in batch:
                entry = result.get(pmid)
                if entry and "error" not in entry:
                    resolved.add(pmid)
        return resolved


def _parse_efetch(xml_text: str) -> list[Citation]:
    citations: list[Citation] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return citations
    for art in root.iter("PubmedArticle"):
        pmid_el = art.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text.strip()
        title_el = art.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""
        abstract = " ".join(
            "".join(a.itertext()).strip() for a in art.findall(".//AbstractText")
        ).strip()
        year_el = art.find(".//PubDate/Year")
        year = int(year_el.text) if year_el is not None and year_el.text and year_el.text.isdigit() else None
        ptypes = [p.text for p in art.findall(".//PublicationType") if p.text]
        ncts = _extract_ncts(art, title, abstract)
        extra = {"NCT": ",".join(ncts)} if ncts else {}
        citations.append(
            Citation(
                id_type=IdType.PMID,
                id=pmid,
                source="pubmed",
                title=title,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                snippet=abstract,
                study_type="; ".join(ptypes) or None,
                year=year,
                extra_ids=extra,
            )
        )
    return citations


_NCT_RE = re.compile(r"NCT\d{8}", re.I)


def _extract_ncts(art, title: str, abstract: str) -> list[str]:
    """ClinicalTrials.gov accessions for this article: structured DataBank first,
    then a regex fallback over title+abstract. Preserves order, dedups."""
    found: list[str] = []
    for acc in art.findall(".//DataBankList/DataBank/AccessionNumberList/AccessionNumber"):
        if acc.text and _NCT_RE.fullmatch(acc.text.strip()):
            found.append(acc.text.strip().upper())
    for m in _NCT_RE.finditer(f"{title} {abstract}"):
        found.append(m.group(0).upper())
    return list(dict.fromkeys(found))
