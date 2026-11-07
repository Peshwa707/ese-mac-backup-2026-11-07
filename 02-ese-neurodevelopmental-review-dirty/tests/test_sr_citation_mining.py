"""Tests for backward/forward citation mining."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import respx

from core.connectors.pubmed import PubMedConnector
from core.sr.citation_mining import mine_citations


def _elink_side_effect(request):
    ln = parse_qs(urlparse(str(request.url)).query).get("linkname", [""])[0]
    links = {"pubmed_pubmed_refs": ["100", "101"], "pubmed_pubmed_citedin": ["200"]}.get(ln, [])
    return httpx.Response(200, json={"linksets": [{"linksetdbs": [{"linkname": ln, "links": links}]}]})


def _esummary_side_effect(request):
    ids = [i for i in parse_qs(urlparse(str(request.url)).query).get("id", [""])[0].split(",") if i]
    return httpx.Response(200, json={"result": {**{i: {"uid": i} for i in ids}, "uids": ids}})


@respx.mock
async def test_mine_citations_finds_new_verified_candidates():
    respx.get(url__regex=r"elink\.fcgi").mock(side_effect=_elink_side_effect)
    respx.get(url__regex=r"esummary\.fcgi").mock(side_effect=_esummary_side_effect)
    conn = PubMedConnector()
    # corpus already has 100; seed is 'S'. Candidates = {101 (ref), 200 (cited)}.
    res = await mine_citations(["S"], corpus_pmids={"100"}, connector=conn)
    await conn.aclose()
    assert res.candidates == ["101", "200"]
    assert res.seeds == 1 and res.from_refs == 1 and res.from_cited_by == 1


@respx.mock
async def test_elink_methods():
    respx.get(url__regex=r"elink\.fcgi").mock(side_effect=_elink_side_effect)
    conn = PubMedConnector()
    assert await conn.references("S") == ["100", "101"]
    assert await conn.cited_by("S") == ["200"]
    await conn.aclose()
