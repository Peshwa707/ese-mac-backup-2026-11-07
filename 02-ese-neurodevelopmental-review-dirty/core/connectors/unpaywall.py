"""Unpaywall open-access resolver (by DOI).

Finds a legal open-access copy (often an author-accepted manuscript) for a DOI and
returns its extracted text. Requires a contact email per Unpaywall's terms
(UNPAYWALL_EMAIL env, default falls back to a generic address). Returns None when
no OA copy exists — paywalled articles are not fabricated.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Optional

from core.connectors.base import Connector
from core.models import Citation, RetrievalResult

_API = "https://api.unpaywall.org/v2"
_TAG_RE = re.compile(r"<[^>]+>")


class UnpaywallConnector(Connector):
    source = "unpaywall"

    def __init__(self, client=None, email: Optional[str] = None):
        super().__init__(client)
        self._email = email or os.environ.get("UNPAYWALL_EMAIL", "research@example.org")

    async def oa_url(self, doi: str) -> Optional[str]:
        try:
            resp = await self._request("GET", f"{_API}/{doi}", params={"email": self._email})
            loc = resp.json().get("best_oa_location") or {}
            return loc.get("url_for_pdf") or loc.get("url")
        except Exception:
            return None

    async def fulltext(self, doi: str) -> Optional[str]:
        url = await self.oa_url(doi)
        if not url:
            return None
        try:
            resp = await self._request("GET", url)
            ctype = resp.headers.get("content-type", "").lower()
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                return _pdf_bytes_to_text(resp.content)
            return _strip_html(resp.text) or None
        except Exception:
            return None

    async def oa_artifact(self, doi: str) -> Optional[tuple[bytes, str, str]]:
        """Download the legal OA copy's raw bytes -> (content, content_type, url), else None.

        Lets a caller SAVE the open-access artifact (PDF/HTML) to disk rather than only
        extract text. Only Unpaywall's best_oa_location is used — no paywalled source.
        """
        url = await self.oa_url(doi)
        if not url:
            return None
        try:
            resp = await self._request("GET", url)
            return resp.content, resp.headers.get("content-type", "").lower(), url
        except Exception:
            return None

    async def search(self, query: str, structured: dict) -> RetrievalResult:
        return self._unavailable("unpaywall: search not used", query)

    async def verify(self, citation: Citation) -> bool:
        return False


def _strip_html(html: str) -> str:
    """Extract readable text without feeding page chrome into an LLM prompt.

    Some legal Unpaywall locations are repository landing pages rather than
    article PDFs. Regex tag removal leaves embedded CSS/JavaScript in the
    result, which can inflate a prompt past a provider's input limit. Prefer a
    parser that removes non-content containers first; retain the conservative
    regex fallback only if malformed HTML defeats parsing.
    """
    if not html:
        return ""
    class _ReadableTextParser(HTMLParser):
        _skip_tags = {"script", "style", "noscript", "template", "svg", "head"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self._skip_depth = 0
            self._parts: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._skip_depth += 1
            elif not self._skip_depth:
                self._parts.append("\n")

        def handle_startendtag(self, tag, attrs):
            if not self._skip_depth:
                self._parts.append("\n")

        def handle_endtag(self, tag):
            if tag in self._skip_tags and self._skip_depth:
                self._skip_depth -= 1
            elif not self._skip_depth:
                self._parts.append("\n")

        def handle_data(self, data):
            if not self._skip_depth:
                self._parts.append(data)

    try:
        parser = _ReadableTextParser()
        parser.feed(html)
        parser.close()
        return re.sub(r"\s+", " ", "".join(parser._parts)).strip()
    except Exception:
        return _TAG_RE.sub(" ", html).replace("&nbsp;", " ").strip()


def _pdf_bytes_to_text(data: bytes) -> Optional[str]:
    import io

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip() or None
    except Exception:
        return None
