"""Pagination tests for external source connectors."""

from __future__ import annotations

import httpx
import respx

from core.connectors.clinicaltrials import ClinicalTrialsConnector
from core.connectors.europepmc import EuropePmcConnector


def _europepmc_record(i: int) -> dict[str, str]:
    return {
        "id": str(100000 + i),
        "source": "MED",
        "pmid": str(100000 + i),
        "title": f"Europe PMC record {i}",
        "pubYear": "2024",
    }


def _ctgov_study(i: int) -> dict[str, dict[str, dict[str, str]]]:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": f"NCT{i:08d}",
                "briefTitle": f"Clinical trial {i}",
            },
            "statusModule": {"startDateStruct": {"date": "2024-01-01"}},
        }
    }


@respx.mock
async def test_europepmc_paginates_until_exhausted():
    route = respx.get(url__regex=r"europepmc/webservices/rest/search").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "hitCount": 250,
                    "nextCursorMark": "page-2",
                    "resultList": {"result": [_europepmc_record(i) for i in range(200)]},
                },
            ),
            httpx.Response(
                200,
                json={
                    "hitCount": 250,
                    "nextCursorMark": "page-2",
                    "resultList": {"result": [_europepmc_record(i) for i in range(200, 250)]},
                },
            ),
        ]
    )

    conn = EuropePmcConnector()
    result = await conn.search("myeloma CAR-T", {})
    await conn.aclose()

    assert result.available is True
    assert len(result.citations) == 250
    assert route.call_count == 2
    assert route.calls[0].request.url.params["cursorMark"] == "*"
    assert route.calls[1].request.url.params["cursorMark"] == "page-2"


@respx.mock
async def test_europepmc_stops_when_cursor_repeats():
    route = respx.get(url__regex=r"europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "hitCount": 2,
                "nextCursorMark": "*",
                "resultList": {"result": [_europepmc_record(1)]},
            },
        )
    )

    conn = EuropePmcConnector()
    result = await conn.search("myeloma", {})
    await conn.aclose()

    assert len(result.citations) == 1
    assert route.call_count == 1


@respx.mock
async def test_europepmc_respects_max_records_cap():
    route = respx.get(url__regex=r"europepmc/webservices/rest/search").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "hitCount": 250,
                    "nextCursorMark": "page-2",
                    "resultList": {"result": [_europepmc_record(i) for i in range(200)]},
                },
            ),
            httpx.Response(
                200,
                json={
                    "hitCount": 250,
                    "nextCursorMark": "page-3",
                    "resultList": {"result": [_europepmc_record(i) for i in range(200, 250)]},
                },
            ),
        ]
    )

    conn = EuropePmcConnector()
    result = await conn.search("myeloma", {}, max_records=205)
    await conn.aclose()

    assert len(result.citations) == 205
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageSize"] == "5"


@respx.mock
async def test_clinicaltrials_paginates_until_token_absent():
    route = respx.get(url__regex=r"clinicaltrials\.gov/api/v2/studies").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "nextPageToken": "page-2",
                    "studies": [_ctgov_study(i) for i in range(20)],
                },
            ),
            httpx.Response(
                200,
                json={"studies": [_ctgov_study(i) for i in range(20, 25)]},
            ),
        ]
    )

    conn = ClinicalTrialsConnector()
    result = await conn.search("osimertinib", {})
    await conn.aclose()

    assert result.available is True
    assert len(result.citations) == 25
    assert route.call_count == 2
    assert "pageToken" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["pageToken"] == "page-2"


@respx.mock
async def test_clinicaltrials_stops_when_token_repeats():
    route = respx.get(url__regex=r"clinicaltrials\.gov/api/v2/studies").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "nextPageToken": "same-token",
                    "studies": [_ctgov_study(1)],
                },
            ),
            httpx.Response(
                200,
                json={
                    "nextPageToken": "same-token",
                    "studies": [_ctgov_study(2)],
                },
            ),
        ]
    )

    conn = ClinicalTrialsConnector()
    result = await conn.search("osimertinib", {})
    await conn.aclose()

    assert len(result.citations) == 2
    assert route.call_count == 2


@respx.mock
async def test_clinicaltrials_respects_max_records_cap():
    route = respx.get(url__regex=r"clinicaltrials\.gov/api/v2/studies").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "nextPageToken": "page-2",
                    "studies": [_ctgov_study(i) for i in range(20)],
                },
            ),
            httpx.Response(
                200,
                json={
                    "nextPageToken": "page-3",
                    "studies": [_ctgov_study(i) for i in range(20, 25)],
                },
            ),
        ]
    )

    conn = ClinicalTrialsConnector()
    result = await conn.search("osimertinib", {}, max_records=21)
    await conn.aclose()

    assert len(result.citations) == 21
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageSize"] == "1"
