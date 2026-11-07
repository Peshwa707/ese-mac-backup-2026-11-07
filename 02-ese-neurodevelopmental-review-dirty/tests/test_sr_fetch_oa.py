"""Tests for the legal open-access harvester (sources, saving, miss, overlay)."""

from __future__ import annotations

import json
from pathlib import Path

from core.sr import fetch_oa
from core.sr.fetch_oa import OAResult, _resolve_one, find_user_pdf, overlay_fulltext, write_report
from core.sr.models import ExtractedStudy
from core.sr.stats.prepare import hr_rows
from core.connectors.unpaywall import _strip_html

_BODY = "<article><body><sec><p>" + "The adjusted odds ratio was 2.06. " * 30 + "</p></sec></body></article>"


class _PMC:
    def __init__(self, xml=None):
        self._xml = xml
    async def fulltext_xml(self, pmid):
        return self._xml
    async def doi_for(self, pmid):
        return None


class _EPMC:
    def __init__(self, loc=None, xml=None):
        self._loc, self._xml = loc, xml
    async def oa_locator(self, pmid):
        return self._loc
    async def oa_fulltext_xml(self, src, rid):
        return self._xml


class _UW:
    def __init__(self, art=None):
        self._art = art
    async def oa_artifact(self, doi):
        return self._art


async def test_pmc_oa_saves_xml(tmp_path: Path):
    r = await _resolve_one("111", "10.1/a", "stem111", tmp_path, _PMC(_BODY), _EPMC(), _UW())
    assert r.source == "pmc"
    assert (tmp_path / "stem111.pmc.xml").exists()
    assert (tmp_path / "stem111.fulltext.txt").exists()


async def test_europepmc_when_pmc_misses(tmp_path: Path):
    r = await _resolve_one("222", "10.1/b", "stem222", tmp_path,
                           _PMC(None), _EPMC(("MED", "222"), _BODY), _UW())
    assert r.source == "europepmc" and (tmp_path / "stem222.epmc.xml").exists()


async def test_unpaywall_pdf_when_others_miss(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fetch_oa, "_pdf_bytes_to_text", lambda b: "X" * 800)
    uw = _UW((b"%PDF-1.4 fake", "application/pdf", "http://x/y.pdf"))
    r = await _resolve_one("333", "10.1/c", "stem333", tmp_path, _PMC(None), _EPMC(), uw)
    assert r.source == "unpaywall" and (tmp_path / "stem333.oa.pdf").exists()


def test_unpaywall_html_strips_page_chrome_and_preserves_text():
    html = "<html><head><style>.noise{font-size:99px}</style></head><body>"
    html += "<script>window.noise = 'x'</script><article><h1>Title</h1>"
    html += "<p>Readable study text.</p></article></body></html>"
    text = _strip_html(html)
    assert "Readable study text." in text
    assert "font-size" not in text and "window.noise" not in text


async def test_miss_reports_institutional_access(tmp_path: Path):
    r = await _resolve_one("444", "10.1/d", "stem444", tmp_path, _PMC(None), _EPMC(), _UW())
    assert r.source == "none" and "institutional access" in r.note
    assert not any(tmp_path.iterdir())  # nothing fabricated on disk


def test_find_user_pdf_robust_to_messy_names(tmp_path: Path):
    (tmp_path / "10.1007:s00535-004-1537-0.pdf").write_bytes(b"%PDF")      # ':' not '_'
    (tmp_path / "10.1002_ijc.2910470102.pdf .pdf").write_bytes(b"%PDF")    # double-ext + space
    (tmp_path / "stemX.oa.pdf").write_bytes(b"%PDF")                       # harvester's own
    assert find_user_pdf(tmp_path, "15830290", "10.1007/s00535-004-1537-0").name.startswith("10.1007")
    assert find_user_pdf(tmp_path, "1845960", "10.1002/ijc.2910470102") is not None
    assert find_user_pdf(tmp_path, "999", "10.9/none") is None            # no false match
    # plain <pmid>.pdf also matches
    (tmp_path / "8268766.pdf").write_bytes(b"%PDF")
    assert find_user_pdf(tmp_path, "8268766", "").name == "8268766.pdf"


def test_write_report(tmp_path: Path):
    res = [OAResult("1", "10/a", "pmc", str(tmp_path / "a.xml"), 900),
           OAResult("2", "10/b", "none", "", 0, "needs institutional access")]
    write_report(res, tmp_path)
    rep = json.loads((tmp_path / "fetch_report.json").read_text())
    assert rep["summary"]["with_fulltext"] == 1 and rep["summary"]["abstract_only"] == 1
    assert rep["pmid_to_file"]["1"].endswith("a.xml") and "2" not in rep["pmid_to_file"]


def test_overlay_fills_nr_then_pools(tmp_path: Path):
    study = {"pmid": "555", "study_name": "X", "modality": "", "values": [
        {"field": "effect_cholesterol", "group": "efficacy", "value": "NR", "flag": "NR", "quote": ""}]}
    (tmp_path / "extractions_merged.json").write_text(json.dumps([study]))
    (tmp_path / "fulltext_555.json").write_text(json.dumps(
        {"pmid": "555", "effect_cholesterol": "OR 2.10 (95% CI 1.20-3.00)"}))

    res = overlay_fulltext(tmp_path)
    assert res["n"] == 1 and "555:effect_cholesterol" in res["updated_fields"]

    merged = json.loads((tmp_path / "extractions_merged.json").read_text())
    v = merged[0]["values"][0]
    assert v["flag"] == "" and v["value"].startswith("OR 2.10")
    # and it now pools
    es = ExtractedStudy(**merged[0])
    rows, _ = hr_rows([es], "effect_cholesterol", {"555": "cohort"})
    assert len(rows) == 1 and abs(rows[0].hr - 2.10) < 1e-6
