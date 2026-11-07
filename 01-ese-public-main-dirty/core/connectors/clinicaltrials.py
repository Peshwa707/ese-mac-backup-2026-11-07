"""ClinicalTrials.gov connector via the public v2 REST API."""

from __future__ import annotations

import re
from typing import Any

from core.connectors.base import Connector
from core.models import Citation, IdType, RetrievalResult

_BASE = "https://clinicaltrials.gov/api/v2"

# Characters that cause HTTP 400 on CT.gov v2: percent-encoded sequences (%),
# colons, quotes, and parentheses are all known bad actors.
_STRIP_RE = re.compile(r'[%:"\'\(\)]')
_WHITESPACE_RE = re.compile(r'\s+')

_MAX_WORDS = 6
_MAX_CHARS = 120


def _sanitize_query(raw: str) -> str:
    """Return a CT.gov-safe query string: strip bad chars, cap length."""
    cleaned = _STRIP_RE.sub(" ", raw)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    # Cap to first _MAX_WORDS words and _MAX_CHARS characters.
    words = cleaned.split()
    capped = " ".join(words[:_MAX_WORDS])
    return capped[:_MAX_CHARS]


# A 4-digit year anywhere in the date string. ClinicalTrials.gov v2 dates may be
# "YYYY-MM-DD", "YYYY-MM", or month-precision "Month YYYY" — taking start[:4]
# silently yielded None for the month-first form, dropping the recency signal.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _parse_year(date_str: str) -> int | None:
    m = _YEAR_RE.search(date_str or "")
    return int(m.group(0)) if m else None


class ClinicalTrialsConnector(Connector):
    source = "clinicaltrials"

    def __init__(self, client=None, page_size: int = 20):
        super().__init__(client)
        self.page_size = page_size

    async def search(
        self, query: str, structured: dict[str, Any], max_records: int = 5000
    ) -> RetrievalResult:
        """Search ClinicalTrials.gov, paginating up to ``max_records`` studies."""
        try:
            safe_query = _sanitize_query(query)
            citations: list[Citation] = []
            page_token: str | None = None
            seen_tokens: set[str] = set()
            cap = max(0, max_records)
            while len(citations) < cap:
                if page_token is not None:
                    if page_token in seen_tokens:
                        break
                    seen_tokens.add(page_token)
                params = {
                    # CT.gov v2 ANDs distinct query.* params, so setting BOTH query.cond
                    # AND query.term to the full string requires the whole query to match
                    # as a condition — near-zero recall. Use the broad cross-field search.
                    "query.term": safe_query,
                    "pageSize": min(self.page_size, cap - len(citations)),
                    "format": "json",
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = await self._request("GET", f"{_BASE}/studies", params=params)
                data = resp.json()
                studies = data.get("studies", [])
                before_count = len(citations)
                for s in studies:
                    if len(citations) >= cap:
                        break
                    if (c := _to_citation(s)) is not None:
                        citations.append(c)
                next_token = data.get("nextPageToken")
                if len(citations) == before_count:
                    break
                if not next_token:
                    break
                page_token = next_token
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:
            return self._unavailable(f"clinicaltrials error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        if citation.id_type != IdType.NCT:
            return False
        try:
            resp = await self._request(
                "GET", f"{_BASE}/studies/{citation.id}", params={"format": "json"}
            )
            data = resp.json()
            ident = (
                data.get("protocolSection", {})
                .get("identificationModule", {})
                .get("nctId")
            )
            return ident == citation.id
        except Exception:
            return False


def _to_citation(study: dict[str, Any]) -> Citation | None:
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    nct = ident.get("nctId")
    if not nct:
        return None
    title = ident.get("briefTitle") or ident.get("officialTitle") or ""
    summary = proto.get("descriptionModule", {}).get("briefSummary", "")
    phases = proto.get("designModule", {}).get("phases", [])
    start = proto.get("statusModule", {}).get("startDateStruct", {}).get("date", "")
    year = _parse_year(start)
    return Citation(
        id_type=IdType.NCT,
        id=nct,
        source="clinicaltrials",
        title=title,
        url=f"https://clinicaltrials.gov/study/{nct}",
        snippet=summary,
        study_type="; ".join(phases) or "clinical_trial",
        year=year,
    )
