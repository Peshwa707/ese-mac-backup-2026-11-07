"""Citation mining — find studies the database search missed (protocol Step 3b).

Backward (reference lists) and forward (cited-by) mining over seed studies via
NCBI elink, then keep only verified PMIDs not already in the corpus. These
candidates feed the normal screening stage — strengthening completeness for any
review type (and a cheap way to backfill a rapid review's restricted search).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from core.connectors.pubmed import PubMedConnector


@dataclass
class MiningResult:
    candidates: list[str] = field(default_factory=list)   # verified, new-to-corpus PMIDs
    from_refs: int = 0
    from_cited_by: int = 0
    seeds: int = 0


async def mine_citations(
    seed_pmids: list[str],
    corpus_pmids: set[str],
    *,
    connector: PubMedConnector | None = None,
    backward: bool = True,
    forward: bool = True,
    concurrency: int = 4,
) -> MiningResult:
    own = connector is None
    connector = connector or PubMedConnector()
    sem = asyncio.Semaphore(concurrency)

    async def _gather(pmid: str) -> tuple[list[str], list[str]]:
        async with sem:
            refs = await connector.references(pmid) if backward else []
            cited = await connector.cited_by(pmid) if forward else []
            return refs, cited

    try:
        results = await asyncio.gather(*(_gather(p) for p in seed_pmids))
        ref_set: set[str] = set()
        cited_set: set[str] = set()
        for refs, cited in results:
            ref_set.update(refs)
            cited_set.update(cited)
        # Candidates = referenced/citing PMIDs not already in the corpus or seeds.
        known = corpus_pmids | set(seed_pmids)
        candidate_ids = (ref_set | cited_set) - known
        verified = await connector.verify_bulk(sorted(candidate_ids)) if candidate_ids else set()
        return MiningResult(
            candidates=sorted(verified),
            from_refs=len(ref_set - known),
            from_cited_by=len(cited_set - known),
            seeds=len(seed_pmids),
        )
    finally:
        if own:
            await connector.aclose()
