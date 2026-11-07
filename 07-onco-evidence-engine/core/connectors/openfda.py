"""openFDA drug-label connector.

Drug labels have no scholarly id, so citations are keyed by the SPL set id and
carry a resolvable DailyMed URL as their handle.
"""

from __future__ import annotations

import re
from typing import Any

from core.connectors.base import Connector
from core.models import Citation, IdType, RetrievalResult

_BASE = "https://api.fda.gov/drug/label.json"
_DAILYMED = "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid="

# Tokens that are too generic to act as relevance signals (stopwords + common
# clinical structural words that appear in nearly every label).
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "in", "for", "and", "or", "with", "to",
        "is", "are", "that", "as", "at", "by", "be", "from", "on", "this",
        "it", "its", "not", "was", "has", "have", "been", "but", "no",
        "patients", "patient", "treatment", "therapy", "drug", "dose",
        "use", "used", "using", "indicated", "indication",
    }
)

# Minimum token length to be a useful keyword.
_MIN_TOKEN_LEN = 3


def _extract_keywords(query: str) -> list[str]:
    """Return lower-case significant words from the query for relevance matching."""
    tokens = re.findall(r"[a-zA-Z0-9]+", query)
    return [
        t.lower()
        for t in tokens
        if len(t) >= _MIN_TOKEN_LEN and t.lower() not in _STOPWORDS
    ]


def _label_is_relevant(record: dict[str, Any], keywords: list[str]) -> bool:
    """Return True if the label is relevant to the query keywords.

    Relevance is established when at least one keyword appears in ANY of:
    - the indications_and_usage text (primary check), OR
    - the drug's brand or generic name (so a query naming a specific drug keeps
      that drug's label even if the indications text doesn't repeat the
      generic/INN name).
    """
    if not keywords:
        # No keywords extracted — can't filter, keep everything to avoid
        # silently dropping all results on a trivial query.
        return True
    indications = record.get("indications_and_usage") or []
    indications_text = (
        " ".join(indications).lower() if isinstance(indications, list) else ""
    )
    openfda = record.get("openfda", {})
    brand_names = [n.lower() for n in (openfda.get("brand_name") or [])]
    generic_names = [n.lower() for n in (openfda.get("generic_name") or [])]
    # Build a combined searchable text: indications + drug names.
    searchable = " ".join([indications_text] + brand_names + generic_names)
    return any(kw in searchable for kw in keywords)


def _escape_fda(value: str) -> str:
    """Backslash-escape the characters that are special inside an openFDA
    (Lucene-style) quoted search value, so a value containing a double quote
    cannot break out of the quoting and alter the query. Without this, a
    fabricated/poisoned citation id like ``abc" OR set_id:*`` would turn the
    verify round-trip into a match against an UNRELATED label — defeating the
    ID-verification anti-hallucination gate."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class OpenFdaConnector(Connector):
    source = "openfda"

    def __init__(self, client=None, limit: int = 10):
        super().__init__(client)
        self.limit = limit

    async def search(self, query: str, structured: dict[str, Any]) -> RetrievalResult:
        try:
            resp = await self._request(
                "GET", _BASE, params={"search": _escape_fda(query), "limit": self.limit}
            )
            results = resp.json().get("results", [])
            keywords = _extract_keywords(query)
            # Post-filter: keep only labels relevant to the query condition.
            relevant = [r for r in results if _label_is_relevant(r, keywords)]
            citations = [c for r in relevant if (c := _to_citation(r)) is not None]
            # Return available=True even when nothing passes the filter — empty
            # is correct; returning junk is not.
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:
            return self._unavailable(f"openfda error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        if citation.id_type != IdType.URL or not citation.id:
            return False
        try:
            resp = await self._request(
                "GET",
                _BASE,
                params={"search": f'set_id:"{_escape_fda(citation.id)}"', "limit": 1},
            )
            results = resp.json().get("results")
            if not results:
                return False
            # Content-identity check: if the citation has a title, verify that
            # the returned drug name is consistent (case-insensitive substring
            # match in either direction).
            if not citation.title:
                # No title — fall back to existence check (current behaviour).
                return True
            record = results[0]
            openfda = record.get("openfda", {})
            brand = (openfda.get("brand_name") or [""])[0].lower()
            generic = (openfda.get("generic_name") or [""])[0].lower()
            citation_title_lower = citation.title.lower()
            # Accept if either drug name is a substring of the title, or the
            # title is a substring of either drug name (handles abbreviations).
            for drug_name in (brand, generic):
                if not drug_name:
                    continue
                if drug_name in citation_title_lower or citation_title_lower in drug_name:
                    return True
            return False
        except Exception:
            return False


def _to_citation(record: dict[str, Any]) -> Citation | None:
    set_id = record.get("set_id")
    if not set_id:
        return None
    openfda = record.get("openfda", {})
    brand = (openfda.get("brand_name") or [""])[0]
    generic = (openfda.get("generic_name") or [""])[0]
    title = " / ".join(p for p in (brand, generic) if p) or "Drug label"
    indications = record.get("indications_and_usage") or record.get("description") or []
    snippet = (indications[0] if isinstance(indications, list) and indications else "")[:2000]
    return Citation(
        id_type=IdType.URL,
        id=set_id,
        source="openfda",
        title=title,
        url=f"{_DAILYMED}{set_id}",
        snippet=snippet,
        study_type="label",
    )
