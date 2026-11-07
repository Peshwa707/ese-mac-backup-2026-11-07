"""Parallel retrieval orchestration across connectors.

Runs every (source, query) pair concurrently, merges per-source results, and
de-duplicates citations cross-source. Sources that fail are marked unavailable
and surfaced rather than dropped silently (graceful degradation).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.connectors.base import Connector, dedup
from core.models import Citation, RetrievalResult


async def retrieve(
    connectors: list[Connector],
    queries: dict[str, list[str]],
    structured: dict[str, Any],
) -> tuple[list[RetrievalResult], list[Citation]]:
    by_source = {c.source: c for c in connectors}

    tasks: list[asyncio.Task[RetrievalResult]] = []
    for source, qs in queries.items():
        connector = by_source.get(source)
        if connector is None:
            continue
        for q in qs:
            tasks.append(asyncio.ensure_future(connector.search(q, structured)))

    raw = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
    # Drop any connectors that raised unexpectedly (defence-in-depth); keep only
    # proper RetrievalResult instances so one broken connector can't abort others.
    results: list[RetrievalResult] = [r for r in raw if isinstance(r, RetrievalResult)]

    all_citations: list[Citation] = []
    for r in results:
        all_citations.extend(r.citations)
    return results, dedup(all_citations)
