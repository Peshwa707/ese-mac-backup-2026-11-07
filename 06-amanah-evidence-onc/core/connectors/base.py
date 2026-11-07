"""Connector ABC, shared HTTP with backoff, and cross-source dedup.

Every connector fails *soft*: on persistent error it returns an unavailable
``RetrievalResult`` rather than raising, so one dead source never sinks the run
(spec §8 per-source graceful degradation).
"""

from __future__ import annotations

import abc
import asyncio
from typing import Any, Optional

import httpx

import re

from core.models import Citation, IdType, RetrievalResult

_WS_RE = re.compile(r"\s+")

_DEFAULT_TIMEOUT = httpx.Timeout(20.0)
_MAX_RETRIES = 4


class Connector(abc.ABC):
    #: Stable source label used in citations, provenance, and source-status.
    source: str = "unknown"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """HTTP with exponential backoff honoring Retry-After. Raises on give-up.

        Only retries on transient errors (429, 5xx, or network-level failures).
        Non-transient 4xx responses (e.g. 404, 403, 400) are raised immediately
        so callers don't waste 15s of sleep on an invented / missing id.
        """
        client = await self._get_client()
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.request(method, url, **kwargs)
                # Transient — back off and retry. Capture a status-carrying
                # exception so that if every attempt is transient, the final
                # raise reports the real HTTP status (e.g. a 429 throttle or a
                # persistent 503) instead of a generic "request failed" — a
                # degraded source must be distinguishable from a network blip.
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"transient {resp.status_code} from {url}",
                        request=resp.request,
                        response=resp,
                    )
                    retry_after = resp.headers.get("retry-after")
                    wait = float(retry_after) if retry_after else delay
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                # Non-transient 4xx — raise immediately, no retry.
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError:
                # Already raised by raise_for_status(); re-raise without retry.
                raise
            except httpx.HTTPError as exc:  # transport/network errors — retry
                last_exc = exc
                await asyncio.sleep(delay)
                delay *= 2
        raise last_exc or httpx.HTTPError(f"request failed: {url}")

    def _unavailable(self, note: str, query: str = "") -> RetrievalResult:
        return RetrievalResult(source=self.source, query=query, available=False, note=note)

    @abc.abstractmethod
    async def search(self, query: str, structured: dict[str, Any]) -> RetrievalResult:
        """Run one query; return citations (or an unavailable result)."""

    @abc.abstractmethod
    async def verify(self, citation: Citation) -> bool:
        """Resolve the citation id against the live API (real round-trip)."""


def _canonical_keys(c: Citation) -> list[str]:
    """The identifiers a study can be matched on across databases."""
    keys: list[str] = []
    if c.doi:
        keys.append(f"DOI:{c.doi.strip().lower()}")
    if c.id_type == IdType.PMID:
        keys.append(f"PMID:{c.id.strip()}")
    if c.id_type == IdType.DOI:
        keys.append(f"DOI:{c.id.strip().lower()}")
    for t, v in (c.extra_ids or {}).items():
        if t in ("PMID", "DOI") and v:
            keys.append(f"{t}:{v.strip().lower() if t == 'DOI' else v.strip()}")
    title = _WS_RE.sub(" ", (c.title or "").lower()).strip()
    if title:
        keys.append(f"T:{title[:80]}|{c.year or ''}")
    return keys


def _merge_two(a: Citation, b: Citation) -> Citation:
    """Fold b into a: keep the richest fields, union all identifiers + sources."""
    if len(b.snippet) > len(a.snippet):
        a.snippet = b.snippet
    if not a.title and b.title:
        a.title = b.title
    a.doi = a.doi or b.doi or (b.id if b.id_type == IdType.DOI else "")
    a.year = a.year or b.year
    a.study_type = a.study_type or b.study_type
    # Record every identifier and source so a merged study is fully traceable.
    a.extra_ids.setdefault(a.id_type.value, a.id)
    b_ids = {b.id_type.value: b.id, **(b.extra_ids or {})}
    for t, v in b_ids.items():
        a.extra_ids.setdefault(t, v)
    srcs = set(filter(None, (a.extra_ids.get("sources", "") + "," + a.source + "," + b.source).split(",")))
    a.extra_ids["sources"] = ",".join(sorted(srcs))
    if b.verification == "live":
        a.verification = "live"
    a.provenance = a.provenance or b.provenance
    return a


def merge_dedup(citations: list[Citation]) -> list[Citation]:
    """Cross-source dedup: collapse a study found in multiple databases into one
    canonical Citation, matching by DOI, then PMID, then normalized title+year."""
    clusters: list[Citation] = []
    key_to_idx: dict[str, int] = {}
    for c in citations:
        keys = _canonical_keys(c)
        idx = next((key_to_idx[k] for k in keys if k in key_to_idx), None)
        if idx is None:
            clusters.append(c.model_copy(deep=True))
            idx = len(clusters) - 1
        else:
            clusters[idx] = _merge_two(clusters[idx], c)
        for k in _canonical_keys(clusters[idx]):
            key_to_idx[k] = idx
    return clusters


def dedup(citations: list[Citation]) -> list[Citation]:
    """Collapse duplicate citations by normalized id key, keeping the richest."""
    by_key: dict[str, Citation] = {}
    for c in citations:
        existing = by_key.get(c.key)
        if existing is None:
            by_key[c.key] = c
            continue
        # Prefer the entry carrying an abstract/snippet, then a title.
        if len(c.snippet) > len(existing.snippet) or (
            not existing.title and c.title
        ):
            by_key[c.key] = c
    return list(by_key.values())
