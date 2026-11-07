"""Guideline connector — interface + NCI PDQ implementation.

PDQ health-professional summaries are US-government, public-domain content. The
active vertical supplies the candidate summary references (URL + match terms)
via ``guideline_refs.yaml``; this connector selects relevant ones for the query
and fetches their prose. The clean ``GuidelinesConnector`` seam is where a
licensed NCCN source drops in later with zero changes elsewhere (spec §7).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from core.connectors.base import Connector
from core.models import Citation, IdType, RetrievalResult

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Constant allowlist of known NCI/guideline hosts — always permitted regardless
# of what guideline_refs.yaml contains.
_CONSTANT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"www.cancer.gov", "cancer.gov"}
)

# Generic fallback used only if the vertical supplies no connector config; the
# real oncology key/title live in verticals/oncology/schema.yaml, not here.
_FALLBACK_REF_KEY = "summaries"
_FALLBACK_TITLE = "guideline summary"


class GuidelinesConnector(Connector):
    """Marker base so alternate guideline sources share one type/seam."""

    source = "guidelines"


class PdqConnector(GuidelinesConnector):
    source = "pdq"

    def __init__(
        self,
        client=None,
        refs: list[dict[str, Any]] | None = None,
        max_refs: int = 4,
        ref_key: str | None = None,
        default_title: str | None = None,
    ):
        super().__init__(client)
        self._refs = refs  # injected for tests; otherwise read from the vertical
        self.max_refs = max_refs
        # Which guideline_refs.yaml key holds this connector's reference list and
        # the fallback title — injectable; otherwise resolved from the vertical.
        self._ref_key = ref_key
        self._default_title = default_title

    def _connector_config(self) -> dict[str, Any]:
        try:
            from core.config.vertical import load_vertical
        except ImportError:
            return {}
        return (
            load_vertical().schema.get("connectors", {}).get("guidelines", {}) or {}
        )

    def _resolve_ref_key(self) -> str:
        if self._ref_key is not None:
            return self._ref_key
        return self._connector_config().get("ref_key", _FALLBACK_REF_KEY)

    def _resolve_default_title(self) -> str:
        if self._default_title is not None:
            return self._default_title
        return self._connector_config().get("default_title", _FALLBACK_TITLE)

    def _candidate_refs(self, query: str) -> list[dict[str, Any]]:
        refs = self._refs
        if refs is None:
            try:
                from core.config.vertical import load_vertical
            except ImportError:
                refs = []
            else:
                refs = load_vertical().guideline_refs.get(self._resolve_ref_key(), [])
        q = query.lower()
        matched = [
            r for r in refs if any(term.lower() in q for term in r.get("match", []))
        ]
        # Fall back to a few defaults so guideline coverage is never silently empty.
        chosen = matched or refs
        return chosen[: self.max_refs]

    def _allowed_hosts(self) -> frozenset[str]:
        """Build the SSRF allowlist: constant hosts UNION hosts in configured refs."""
        refs = self._refs
        if refs is None:
            try:
                from core.config.vertical import load_vertical

                refs = load_vertical().guideline_refs.get(self._resolve_ref_key(), [])
            except Exception:
                refs = []
        dynamic: set[str] = set()
        for r in refs:
            url = r.get("url", "")
            if url:
                try:
                    host = urlparse(url).hostname or ""
                    if host:
                        dynamic.add(host.lower())
                except Exception:
                    pass
        return _CONSTANT_ALLOWED_HOSTS | frozenset(dynamic)

    def _validate_url(self, url: str) -> bool:
        """Return True iff the URL is safe to fetch (https + allowlisted host)."""
        try:
            parsed = urlparse(url)
            if parsed.scheme != "https":
                return False
            host = (parsed.hostname or "").lower()
            return host in self._allowed_hosts()
        except Exception:
            return False

    async def search(self, query: str, structured: dict[str, Any]) -> RetrievalResult:
        try:
            default_title = self._resolve_default_title()
            citations: list[Citation] = []
            for ref in self._candidate_refs(query):
                url = ref.get("url")
                if not url:
                    continue
                if not self._validate_url(url):
                    continue  # skip disallowed URLs silently
                try:
                    resp = await self._request("GET", url)
                    citations.append(
                        Citation(
                            id_type=IdType.URL,
                            id=url,
                            source=self.source,
                            title=ref.get("title", default_title),
                            url=url,
                            snippet=_html_to_text(resp.text)[:2000],
                            study_type="guideline",
                        )
                    )
                except Exception:
                    continue  # one bad summary doesn't sink the source
            if not citations:
                return self._unavailable("no guideline summaries reachable", query)
            return RetrievalResult(source=self.source, query=query, citations=citations)
        except Exception as exc:
            return self._unavailable(f"pdq error: {exc}", query)

    async def verify(self, citation: Citation) -> bool:
        if citation.id_type != IdType.URL or not citation.id:
            return False
        if not self._validate_url(citation.id):
            return False
        try:
            resp = await self._request("GET", citation.id)
            if resp.status_code != 200:
                return False
            # Content-identity check: if we have a stored snippet, require it
            # to appear in the fetched page (normalised whitespace/case).
            if citation.snippet:
                page_text = _normalise(resp.text)
                needle = _normalise(citation.snippet[:200])  # leading 200 chars
                return needle in page_text
            # No snippet — fall back to liveness (200 is sufficient).
            return True
        except Exception:
            return False


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


def _normalise(text: str) -> str:
    """Strip HTML tags, collapse whitespace, lowercase — for snippet matching."""
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip().lower()
