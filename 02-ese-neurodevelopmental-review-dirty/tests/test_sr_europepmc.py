"""Tests for the Europe PMC connector."""

from __future__ import annotations

import httpx
import respx

from core.connectors.europepmc import EuropePmcConnector
from core.models import IdType

_RESP = {"hitCount": 2, "resultList": {"result": [
    {"id": "111", "source": "MED", "pmid": "111", "doi": "10.1/a",
     "title": "CAR-T in myeloma", "abstractText": "ORR 85%.", "pubYear": "2024", "pubType": "journal article"},
    {"id": "PPR1", "source": "PPR", "doi": "10.2/preprint",
     "title": "Bispecific preprint", "abstractText": "Phase 1.", "pubYear": "2025"},
]}}


@respx.mock
async def test_europepmc_search_parses_pmid_and_doi():
    respx.get(url__regex=r"europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(200, json=_RESP))
    conn = EuropePmcConnector()
    res = await conn.search("myeloma CAR-T", {})
    await conn.aclose()
    assert res.available and len(res.citations) == 2
    pmid_c = next(c for c in res.citations if c.id_type == IdType.PMID)
    assert pmid_c.id == "111" and pmid_c.doi == "10.1/a" and pmid_c.year == 2024
    # Preprint with no PMID falls back to DOI id.
    doi_c = next(c for c in res.citations if c.id_type == IdType.DOI)
    assert doi_c.id == "10.2/preprint" and doi_c.source == "europepmc"


@respx.mock
async def test_europepmc_verify_hitcount():
    respx.get(url__regex=r"europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(200, json={"hitCount": 1, "resultList": {"result": []}}))
    from core.models import Citation
    conn = EuropePmcConnector()
    ok = await conn.verify(Citation(id_type=IdType.PMID, id="111", source="europepmc"))
    await conn.aclose()
    assert ok is True
