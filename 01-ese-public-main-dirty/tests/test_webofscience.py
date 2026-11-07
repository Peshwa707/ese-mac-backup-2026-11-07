"""Web of Science Starter connector — offline contract tests (mocked HTTP)."""
import asyncio

from core.connectors.webofscience import WebOfScienceConnector, _to_citation
from core.models import IdType


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


_HIT_PMID = {
    "uid": "WOS:000123", "title": "AT in preeclampsia",
    "source": {"sourceTitle": "J Obstet", "publishYear": 2020},
    "identifiers": {"doi": "10.1/x", "pmid": "32780999"},
}
_HIT_DOI_ONLY = {"uid": "WOS:000456", "title": "DOI only",
                 "source": {"publishYear": 2019}, "identifiers": {"doi": "10.2/y"}}
_HIT_UID_ONLY = {"uid": "WOS:000789", "title": "UID only",
                 "source": {"publishYear": "2018"}, "identifiers": {}}


def test_to_citation_prefers_pmid():
    c = _to_citation(_HIT_PMID)
    assert c.id_type == IdType.PMID and c.id == "32780999"
    assert c.doi == "10.1/x" and c.year == 2020 and c.source == "webofscience"
    assert c.extra_ids.get("WOS") == "WOS:000123" and c.provenance == ""  # verifies live


def test_to_citation_doi_when_no_pmid():
    c = _to_citation(_HIT_DOI_ONLY)
    assert c.id_type == IdType.DOI and c.id == "10.2/y" and c.year == 2019


def test_to_citation_uid_only_is_unverified_not_attested():
    c = _to_citation(_HIT_UID_ONLY)
    assert c.id_type == IdType.URL and c.id == "WOS:000789"
    # No provenance attestation: a UID-only record has no PMID/DOI to verify, so it
    # stays unverified and cite-or-omit drops it (not conflated with imported exports).
    assert c.provenance == ""
    assert c.extra_ids.get("WOS") == "WOS:000789" and c.year == 2018


def test_truncated_pagination_degrades_to_unavailable(monkeypatch):
    # page 1 gives 2 of a claimed 5; page 2 empty → incomplete → unavailable.
    conn = WebOfScienceConnector(api_key="k", page_size=2)
    pages = {
        1: {"metadata": {"total": 5}, "hits": [_HIT_PMID, _HIT_DOI_ONLY]},
        2: {"metadata": {"total": 5}, "hits": []},
    }

    async def _fake_request(method, url, **kwargs):
        return _Resp(pages[kwargs["params"]["page"]])

    monkeypatch.setattr(conn, "_request", _fake_request)
    res = asyncio.run(conn.search("TS=(x)", {}))
    assert res.available is False and "incomplete" in res.note


def test_pagination_holds_limit_constant_across_pages(monkeypatch):
    # Two pages of 2, cap high enough to fetch both; assert the request `limit` is
    # constant and `page` increments (guards the offset-corruption fix).
    conn = WebOfScienceConnector(api_key="k", page_size=2, max_records=10)
    seen = []
    pages = {
        1: {"metadata": {"total": 4}, "hits": [_HIT_PMID, _HIT_DOI_ONLY]},
        2: {"metadata": {"total": 4}, "hits": [_HIT_UID_ONLY,
             {"uid": "WOS:9", "title": "p2b", "source": {"publishYear": 2017},
              "identifiers": {"pmid": "11057854"}}]},
        3: {"metadata": {"total": 4}, "hits": []},
    }

    async def _fake_request(method, url, **kwargs):
        seen.append((kwargs["params"]["limit"], kwargs["params"]["page"]))
        return _Resp(pages[kwargs["params"]["page"]])

    monkeypatch.setattr(conn, "_request", _fake_request)
    res = asyncio.run(conn.search("TS=(x)", {}))
    assert res.available is True and len(res.citations) == 4
    assert [lim for lim, _ in seen] == [2, 2]          # limit constant
    assert [pg for _, pg in seen] == [1, 2]            # page increments


def test_search_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("WOS_STARTER_KEY", raising=False)
    # ensure the providers.env path yields nothing for this key
    monkeypatch.setattr("core.connectors.webofscience.search_credential", lambda name: None)
    conn = WebOfScienceConnector()
    res = asyncio.run(conn.search("TS=(antithrombin)", {}))
    assert res.available is False and "WOS_STARTER_KEY" in res.note


def test_search_parses_hits_with_mocked_http(monkeypatch):
    conn = WebOfScienceConnector(api_key="fake-key", page_size=50)

    async def _fake_request(method, url, **kwargs):
        assert kwargs["headers"]["X-ApiKey"] == "fake-key"
        assert kwargs["params"]["db"] == "WOS"
        return _Resp({"metadata": {"total": 2}, "hits": [_HIT_PMID, _HIT_DOI_ONLY]})

    monkeypatch.setattr(conn, "_request", _fake_request)
    res = asyncio.run(conn.search("TS=(antithrombin)", {}))
    assert res.available is True and len(res.citations) == 2
    assert {c.id for c in res.citations} == {"32780999", "10.2/y"}
