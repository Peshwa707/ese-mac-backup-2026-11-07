"""Tests for multi-source foundation: cross-source dedup, DOI verify, gather_corpus."""

from __future__ import annotations

import httpx
import respx

from core.connectors.base import merge_dedup
from core.connectors.crossref import verify_doi
from core.connectors.pubmed import PubMedConnector
from core.models import Citation, IdType, RetrievalResult
from tests.conftest import PUBMED_EFETCH_XML, PUBMED_ESEARCH


def test_merge_dedup_collapses_same_study_across_sources():
    cites = [
        Citation(id_type=IdType.PMID, id="111", source="pubmed", title="A trial", doi="10.1/x", year=2024),
        Citation(id_type=IdType.DOI, id="10.1/x", source="embase", title="A trial", year=2024),
        Citation(id_type=IdType.PMID, id="111", source="europepmc", title="A trial"),
        Citation(id_type=IdType.PMID, id="999", source="pubmed", title="Different study", year=2023),
    ]
    merged = merge_dedup(cites)
    assert len(merged) == 2                       # the three "A trial" records collapse to one
    a = next(c for c in merged if c.title == "A trial")
    assert "pubmed" in a.extra_ids["sources"] and "embase" in a.extra_ids["sources"]
    assert a.extra_ids.get("DOI") == "10.1/x" or a.doi == "10.1/x"


@respx.mock
async def test_verify_doi_matches():
    respx.get(url__regex=r"api\.crossref\.org/works/").mock(
        return_value=httpx.Response(200, json={"message": {"DOI": "10.1/x"}}))
    assert await verify_doi("10.1/x") is True


@respx.mock
async def test_verify_doi_rejects_missing():
    respx.get(url__regex=r"api\.crossref\.org/works/").mock(return_value=httpx.Response(404))
    assert await verify_doi("10.1/missing") is False


class _FakeSource:
    source = "embase"

    async def search(self, query, structured):
        return RetrievalResult(source="embase", query=query, citations=[
            Citation(id_type=IdType.DOI, id="10.5/embase", source="embase",
                     title="Embase-only study", doi="10.5/embase", verification="unverified")])

    async def aclose(self):
        pass


def _esummary(request):
    from urllib.parse import parse_qs, urlparse
    ids = [i for i in parse_qs(urlparse(str(request.url)).query).get("id", [""])[0].split(",") if i]
    return httpx.Response(200, json={"result": {**{i: {"uid": i} for i in ids}, "uids": ids}})


@respx.mock
async def test_gather_corpus_multisource(monkeypatch, tmp_path):
    monkeypatch.setenv("ONCO_PROVENANCE_DIR", str(tmp_path))
    respx.get(url__regex=r"esearch\.fcgi").mock(return_value=httpx.Response(200, json=PUBMED_ESEARCH))
    respx.get(url__regex=r"efetch\.fcgi").mock(return_value=httpx.Response(200, text=PUBMED_EFETCH_XML))
    respx.get(url__regex=r"esummary\.fcgi").mock(side_effect=_esummary)
    respx.get(url__regex=r"api\.crossref\.org/works/").mock(
        return_value=httpx.Response(200, json={"message": {"DOI": "10.5/embase"}}))

    from core.sr.pipeline import gather_corpus
    corpus = await gather_corpus(retmax=10, sources=[PubMedConnector(), _FakeSource()])
    assert corpus.per_source == {"pubmed": 2, "embase": 1}
    assert "embase" in corpus.sources_executed
    # 2 PubMed (PMID-verified) + 1 Embase (DOI-verified) all included.
    assert len(corpus.verified) == 3
    assert any(c.source == "embase" and c.verification == "live" for c in corpus.verified)
