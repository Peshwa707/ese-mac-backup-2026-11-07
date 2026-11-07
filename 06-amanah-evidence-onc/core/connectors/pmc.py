"""PubMed Central open-access full-text connector (NCBI E-utilities).

Resolves a PMID to its PMC id (elink), fetches the full-text XML (efetch from the
``pmc`` db), and extracts the body text. Only open-access articles return text;
paywalled ones resolve to None (honestly — no full text, not fabricated text).
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any, Optional

from core.connectors.base import Connector
from core.models import Citation, RetrievalResult

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PmcConnector(Connector):
    source = "pmc"

    def __init__(self, client=None):
        super().__init__(client)
        self._api_key = os.environ.get("NCBI_API_KEY")

    def _params(self, **kw: Any) -> dict[str, Any]:
        p = dict(kw)
        if self._api_key:
            p["api_key"] = self._api_key
        return p

    async def pmcid_for(self, pmid: str) -> Optional[str]:
        try:
            resp = await self._request(
                "GET", f"{_BASE}/elink.fcgi",
                params=self._params(dbfrom="pubmed", db="pmc", id=pmid,
                                    retmode="json", linkname="pubmed_pmc"),
            )
            for ls in resp.json().get("linksets", []):
                for ldb in ls.get("linksetdbs", []):
                    links = ldb.get("links") or []
                    if links:
                        return f"PMC{links[0]}"
        except Exception:
            return None
        return None

    async def doi_for(self, pmid: str) -> Optional[str]:
        try:
            resp = await self._request(
                "GET", f"{_BASE}/esummary.fcgi",
                params=self._params(db="pubmed", id=pmid, retmode="json"),
            )
            entry = resp.json().get("result", {}).get(pmid, {})
            for aid in entry.get("articleids", []):
                if aid.get("idtype") == "doi":
                    return aid.get("value")
        except Exception:
            return None
        return None

    async def fulltext(self, pmid: str) -> Optional[str]:
        """Return PMC open-access body text for ``pmid``, or None if unavailable."""
        xml = await self.fulltext_xml(pmid)
        if not xml:
            return None
        return _extract_body_text(xml) or None

    async def fulltext_xml(self, pmid: str) -> Optional[str]:
        """Return the raw PMC open-access JATS XML (tables preserved), or None."""
        pmcid = await self.pmcid_for(pmid)
        if not pmcid:
            return None
        try:
            resp = await self._request(
                "GET", f"{_BASE}/efetch.fcgi",
                params=self._params(db="pmc", id=pmcid.replace("PMC", ""), retmode="xml"),
            )
            text = resp.text or ""
            return text if text.strip() else None
        except Exception:
            return None

    # Connector ABC requirements (unused for SR full text but keep the contract).
    async def search(self, query: str, structured: dict[str, Any]) -> RetrievalResult:
        return self._unavailable("pmc: search not used", query)

    async def verify(self, citation: Citation) -> bool:
        return await self.pmcid_for(citation.id) is not None


def _extract_body_text(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    parts: list[str] = []
    for body in root.iter("body"):
        for p in body.iter("p"):
            t = "".join(p.itertext()).strip()
            if t:
                parts.append(t)
    return "\n\n".join(parts).strip()
