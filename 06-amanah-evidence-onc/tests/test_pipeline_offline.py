"""Offline integration test for the no-evidence path.

With every external source unreachable, the engine must produce an explicit
EMPTY report — ``empty_reason`` set, zero findings — and must NEVER fabricate a
finding to fill the gap. This exercises the real pipeline wiring (de-id ->
structure -> retrieval -> verification) with all HTTP mocked to failure and the
LLM stubbed, so it runs without network or an API key.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx

from core import pipeline
from core.connectors import (
    ClinicalTrialsConnector,
    OpenFdaConnector,
    PdqConnector,
    PubMedConnector,
)


def _stub_parse(schema, system, user, **kwargs):
    """Stand in for the structure-extraction LLM call.

    Returns an object whose ``model_dump`` yields an empty structured case. If
    the synthesis stage ever reached the LLM on the no-evidence path that would
    be a fabrication bug — but on this path it is never called (the pipeline
    returns at the empty gate before synthesis).
    """
    obj = MagicMock()
    obj.model_dump.return_value = {}
    return obj


@respx.mock
async def test_no_evidence_yields_empty_report_not_fabrication(monkeypatch):
    # No real backoff waits when sources return 500.
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("core.connectors.base.asyncio.sleep", _no_sleep)
    # Stub the only LLM call on this path (structured extraction).
    monkeypatch.setattr("core.llm.client.parse", _stub_parse)

    # Every external host fails: pubmed/ctgov/openfda -> 500, guideline URLs -> 500.
    respx.route(host="eutils.ncbi.nlm.nih.gov").mock(return_value=httpx.Response(500))
    respx.route(host="clinicaltrials.gov").mock(return_value=httpx.Response(500))
    respx.route(host="api.fda.gov").mock(return_value=httpx.Response(500))
    respx.route(host="www.cancer.gov").mock(return_value=httpx.Response(500))
    # Catch-all so nothing escapes to the network.
    respx.route().mock(return_value=httpx.Response(500))

    connectors = [
        PubMedConnector(),
        ClinicalTrialsConnector(),
        OpenFdaConnector(),
        PdqConnector(),
    ]

    report = await pipeline.run_case(
        "A de-identified patient case with no recoverable evidence.",
        make_manuscript=True,
        connectors=connectors,
    )

    # Explicit empty state — never fabricated content.
    assert report.empty_reason, "expected an explicit empty_reason"
    assert report.decision_support == []
    assert report.manuscript is None
    # Sources are reported, all unavailable.
    assert report.sources_status
    assert all(not s.available for s in report.sources_status)


@respx.mock
async def test_empty_search_results_yield_empty_report(monkeypatch):
    """Sources are UP but return zero hits -> still an empty, non-fabricated report."""

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("core.connectors.base.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("core.llm.client.parse", _stub_parse)

    # PubMed esearch: empty idlist. Everything else: empty-ish 200 payloads.
    respx.get(url__regex=r"esearch\.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    respx.route(host="clinicaltrials.gov").mock(
        return_value=httpx.Response(200, json={"studies": []})
    )
    respx.route(host="api.fda.gov").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.route(host="www.cancer.gov").mock(return_value=httpx.Response(500))
    respx.route().mock(return_value=httpx.Response(200, json={}))

    connectors = [
        PubMedConnector(),
        ClinicalTrialsConnector(),
        OpenFdaConnector(),
        PdqConnector(),
    ]

    report = await pipeline.run_case(
        "A de-identified patient case.",
        connectors=connectors,
    )

    assert report.empty_reason
    assert report.decision_support == []


@respx.mock
async def test_no_injected_connectors_builds_from_vertical(monkeypatch):
    """When the caller injects no connectors, the pipeline builds them from the
    active vertical's configured source set (boundary C4) — here that yields the
    oncology four, all reported in sources_status."""

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("core.connectors.base.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("core.llm.client.parse", _stub_parse)

    # Everything fails -> empty report, but sources_status reflects the built set.
    respx.route().mock(return_value=httpx.Response(500))

    report = await pipeline.run_case(
        "A de-identified patient case with no recoverable evidence.",
    )

    assert report.empty_reason
    sources = {s.source for s in report.sources_status}
    assert sources == {"pubmed", "clinicaltrials", "openfda", "pdq"}
