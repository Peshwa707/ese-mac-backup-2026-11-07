"""Scopus Search connector — offline contract tests (mocked HTTP)."""
import asyncio

from core.connectors.scopus import ScopusConnector, _to_citation
from core.models import IdType


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


_E_PMID = {"dc:title": "AT in preeclampsia", "prism:doi": "10.1/x",
           "pubmed-id": "32780999", "prism:coverDate": "2020-03-01",
           "eid": "2-s2.0-1", "prism:publicationName": "J Obstet"}
_E_DOI = {"dc:title": "DOI only", "prism:doi": "10.2/y",
          "prism:coverDate": "2019-01-01", "eid": "2-s2.0-2"}
_E_EID = {"dc:title": "EID only", "prism:coverDate": "2018-01-01", "eid": "2-s2.0-3"}


def test_to_citation_prefers_pmid():
    c = _to_citation(_E_PMID)
    assert c.id_type == IdType.PMID and c.id == "32780999"
    assert c.doi == "10.1/x" and c.year == 2020 and c.source == "scopus"
    assert c.extra_ids.get("EID") == "2-s2.0-1"


def test_to_citation_doi_then_eid():
    assert _to_citation(_E_DOI).id_type == IdType.DOI and _to_citation(_E_DOI).id == "10.2/y"
    c = _to_citation(_E_EID)
    assert c.id_type == IdType.URL and c.id == "2-s2.0-3" and c.year == 2018
    assert c.provenance == ""   # EID-only stays unverified, not attested


def test_search_without_key_is_unavailable(monkeypatch):
    monkeypatch.setattr("core.connectors.scopus.search_credential", lambda name: None)
    res = asyncio.run(ScopusConnector().search("TITLE-ABS-KEY(x)", {}))
    assert res.available is False and "SCOPUS_API_KEY" in res.note


def test_empty_result_quirk(monkeypatch):
    conn = ScopusConnector(api_key="k")

    async def _fake(method, url, **kwargs):
        return _Resp({"search-results": {"opensearch:totalResults": "0",
                                         "entry": [{"error": "Result set was empty"}]}})

    monkeypatch.setattr(conn, "_request", _fake)
    res = asyncio.run(conn.search("TITLE-ABS-KEY(nothing)", {}))
    assert res.available is True and res.citations == []


def test_truncated_pagination_degrades_to_unavailable(monkeypatch):
    # page 0 gives 2 of a claimed 5; page 1 comes back empty → incomplete → unavailable,
    # rather than silently returning 2 records as a complete Scopus search.
    conn = ScopusConnector(api_key="k", page_size=2)
    pages = {
        0: {"search-results": {"opensearch:totalResults": "5", "entry": [_E_PMID, _E_DOI]}},
        2: {"search-results": {"opensearch:totalResults": "5", "entry": []}},
    }

    async def _fake(method, url, **kwargs):
        return _Resp(pages[kwargs["params"]["start"]])

    monkeypatch.setattr(conn, "_request", _fake)
    res = asyncio.run(conn.search("TITLE-ABS-KEY(x)", {}))
    assert res.available is False and "incomplete" in res.note


def test_credential_error_degrades_not_aborts(monkeypatch):
    # An insecure providers.env makes search_credential raise; it must degrade THIS
    # source to unavailable, not propagate and abort the whole multi-source run.
    def _raise(name):
        raise RuntimeError("providers.env permissions too open (must be 0600)")

    monkeypatch.setattr("core.connectors.scopus.search_credential", _raise)
    res = asyncio.run(ScopusConnector().search("TITLE-ABS-KEY(x)", {}))
    assert res.available is False and "scopus error" in res.note


def test_pagination_constant_count_increments_start(monkeypatch):
    conn = ScopusConnector(api_key="k", insttoken="tok", page_size=2, max_records=10)
    seen = []
    pages = {
        0: {"search-results": {"opensearch:totalResults": "3", "entry": [_E_PMID, _E_DOI]}},
        2: {"search-results": {"opensearch:totalResults": "3", "entry": [_E_EID]}},
    }

    async def _fake(method, url, **kwargs):
        assert kwargs["headers"]["X-ELS-APIKey"] == "k"
        assert kwargs["headers"]["X-ELS-Insttoken"] == "tok"
        seen.append((kwargs["params"]["count"], kwargs["params"]["start"]))
        return _Resp(pages[kwargs["params"]["start"]])

    monkeypatch.setattr(conn, "_request", _fake)
    res = asyncio.run(conn.search("TITLE-ABS-KEY(x)", {}))
    assert len(res.citations) == 3
    assert [cnt for cnt, _ in seen] == [2, 2]       # count constant
    assert [st for _, st in seen] == [0, 2]         # start increments by count
