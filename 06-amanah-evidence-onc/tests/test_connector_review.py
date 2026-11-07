"""Connector correctness fixes from code review (2026-06-04)."""

from __future__ import annotations

import copy

import httpx
import pytest
import respx

import core.connectors.base as base_mod
from core.connectors.clinicaltrials import ClinicalTrialsConnector, _parse_year
from core.connectors.openfda import OpenFdaConnector, _escape_fda
from core.connectors.pubmed import PubMedConnector
from core.models import Citation, IdType
from tests.conftest import CTGOV_STUDIES, OPENFDA_LABEL


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """Don't actually sleep through exponential backoff during retry tests."""
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(base_mod.asyncio, "sleep", _instant)


@respx.mock
async def test_persistent_5xx_raises_status_carrying_error():
    """A source returning persistent 503s must give up with an exception that
    carries the real HTTP status, not a generic 'request failed'."""
    respx.get(url__regex=r"esearch\.fcgi").mock(return_value=httpx.Response(503))
    conn = PubMedConnector()
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await conn._request("GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi")
    assert exc_info.value.response.status_code == 503
    await conn.aclose()


def test_parse_year_handles_month_first_and_partial():
    assert _parse_year("June 2024") == 2024
    assert _parse_year("2021-03") == 2021
    assert _parse_year("2022-01-01") == 2022
    assert _parse_year("") is None
    assert _parse_year("no date here") is None


@respx.mock
async def test_clinicaltrials_year_from_month_first_date():
    payload = copy.deepcopy(CTGOV_STUDIES)
    payload["studies"][0]["protocolSection"]["statusModule"]["startDateStruct"]["date"] = "June 2024"
    respx.get(url__regex=r"/studies").mock(return_value=httpx.Response(200, json=payload))
    conn = ClinicalTrialsConnector()
    result = await conn.search("egfr", {})
    assert result.citations[0].year == 2024
    await conn.aclose()


def test_pubmed_params_include_tool_and_email(monkeypatch):
    monkeypatch.setenv("NCBI_EMAIL", "ops@example.org")
    p = PubMedConnector()._params(term="x")
    assert p["tool"] == "onco-evidence-engine"
    assert p["email"] == "ops@example.org"


def test_escape_fda_neutralizes_quote_breakout():
    assert _escape_fda('abc" OR set_id:*') == 'abc\\" OR set_id:*'
    # Normal set ids / terms are untouched.
    assert _escape_fda("abc-set-id") == "abc-set-id"
    assert _escape_fda("osimertinib NSCLC") == "osimertinib NSCLC"


@respx.mock
async def test_openfda_verify_sends_escaped_id():
    """A citation id containing a double quote must reach openFDA escaped, so it
    cannot break out of the quoted set_id value and match an unrelated label."""
    route = respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=OPENFDA_LABEL)
    )
    conn = OpenFdaConnector()
    cite = Citation(
        id_type=IdType.URL, id='evil" OR set_id:*', source="openfda",
        title="Tagrisso / osimertinib",
    )
    await conn.verify(cite)
    sent = route.calls.last.request.url.params["search"]
    assert '\\"' in sent and 'set_id:"evil\\"' in sent
    await conn.aclose()
