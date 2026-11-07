"""Tests for full-text resolution (PMC parse, source priority, coverage)."""

from __future__ import annotations

import pytest

from core.connectors.pmc import _extract_body_text
from core.sr import fulltext
from core.sr.fulltext import FullText, coverage_summary, ensure_inbox


def test_extract_body_text_pulls_paragraphs():
    xml = """<article><front/><body>
      <sec><title>Results</title><p>The hazard ratio was 0.41.</p>
      <p>MRD negativity reached 60%.</p></sec></body></article>"""
    text = _extract_body_text(xml)
    assert "hazard ratio was 0.41" in text and "MRD negativity reached 60%" in text


class _FakeConn:
    def __init__(self, text=None):
        self._text = text
    async def fulltext(self, key):
        return self._text
    async def doi_for(self, pmid):
        return None
    async def aclose(self):
        pass


async def test_resolve_prefers_user_pdf(tmp_path, monkeypatch):
    (tmp_path / "111.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(fulltext, "pdf_to_text", lambda p: "X" * 800)  # long enough
    ft = await fulltext._resolve_one("111", None, tmp_path, _FakeConn("pmc text"), _FakeConn())
    assert ft.available and ft.source == "user_pdf" and ft.chars == 800


async def test_resolve_falls_back_to_pmc(tmp_path, monkeypatch):
    monkeypatch.setattr(fulltext, "pdf_to_text", lambda p: "")
    ft = await fulltext._resolve_one("222", None, tmp_path, _FakeConn("P" * 600), _FakeConn())
    assert ft.available and ft.source == "pmc"


async def test_resolve_none_when_paywalled(tmp_path, monkeypatch):
    monkeypatch.setattr(fulltext, "pdf_to_text", lambda p: "")
    ft = await fulltext._resolve_one("333", "10.1/x", tmp_path, _FakeConn(None), _FakeConn(None))
    assert not ft.available and ft.source == "none"


def test_coverage_summary():
    res = [FullText("1", True, "pmc", "x", 1), FullText("2", True, "user_pdf", "y", 1),
           FullText("3", False, "none")]
    s = coverage_summary(res)
    assert s["total"] == 3 and s["with_fulltext"] == 2 and s["abstract_only"] == 1
    assert s["by_source"]["pmc"] == 1 and s["by_source"]["user_pdf"] == 1


def test_ensure_inbox_creates_readme(tmp_path):
    box = ensure_inbox(tmp_path / "inbox")
    assert (box / "README.md").exists() and "<PMID>.pdf" in (box / "README.md").read_text()
