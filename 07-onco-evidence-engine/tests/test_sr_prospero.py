"""Tests for the PROSPERO registration draft + duplicate check."""

from __future__ import annotations

import httpx
import respx

from core.sr.config import load_sr_config
from core.sr.prospero import duplicate_check, registration_fields
from tests.test_sr_manuscript import _synthetic_run


def test_registration_fields_populated_and_human_placeholders():
    fields = dict(registration_fields(load_sr_config(), _synthetic_run()))
    assert fields["1. Review title"]
    assert "Population:" in fields["11. Review question"]
    assert "RoB 2.0" in fields["21. Risk of bias (quality) assessment"]
    # Administrative fields are explicit human placeholders, not fabricated.
    assert "[HUMAN" in fields["6. Named contact"]
    assert "[HUMAN" in fields["9. Funding sources / sponsors"]


@respx.mock
async def test_duplicate_check_manual_fallback_on_error():
    respx.get(url__regex=r"prospero/search").mock(return_value=httpx.Response(500))
    dc = await duplicate_check("myeloma")
    assert dc.status == "manual" and "manually" in dc.note.lower()


@respx.mock
async def test_duplicate_check_parses_results():
    html = "<html>PROSPERO <a href='x?recordid=42'>A myeloma review</a></html>"
    respx.get(url__regex=r"prospero/search").mock(return_value=httpx.Response(200, text=html))
    dc = await duplicate_check("myeloma")
    assert dc.status == "checked" and dc.titles == ["A myeloma review"]
