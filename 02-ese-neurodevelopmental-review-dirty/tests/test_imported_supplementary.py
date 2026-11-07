"""Import path — Embase (primary) + Google Scholar (supplementary) ingestion."""
import asyncio

from core.connectors.base import merge_dedup
from core.connectors.imported import (
    ImportedConnector, imported_connectors, is_supplementary, legacy_flat_exports,
    parse_file,
)
from core.models import Citation, IdType

_EMBASE_RIS = """TY  - JOUR
TI  - Antithrombin in preterm preeclampsia
N2  - A randomized trial of AT.
PY  - 2020
DO  - 10.1/embase
C5  - 32780999
M3  - Article
ER  -
"""

_GS_CSV = "Title,Year,DOI\nAntithrombin supplementation review,2019,10.2/gs\n"


def test_is_supplementary_matches_variants():
    assert is_supplementary("GoogleScholar")
    assert is_supplementary("google_scholar")
    assert is_supplementary("Google-Scholar")
    assert is_supplementary("handsearch")
    assert not is_supplementary("Embase")
    assert not is_supplementary("CENTRAL")


def test_embase_ris_is_primary(tmp_path):
    f = tmp_path / "export.ris"
    f.write_text(_EMBASE_RIS, encoding="utf-8")
    cites = parse_file(f, "Embase")
    assert len(cites) == 1
    c = cites[0]
    assert c.source == "Embase" and c.supplementary is False
    assert c.id == "32780999" and c.doi == "10.1/embase" and c.year == 2020
    assert c.verification == "unverified" and c.provenance == "export.ris"


def test_google_scholar_csv_is_supplementary(tmp_path):
    f = tmp_path / "gs.csv"
    f.write_text(_GS_CSV, encoding="utf-8")
    cites = parse_file(f, "GoogleScholar")
    assert len(cites) == 1
    assert cites[0].supplementary is True and cites[0].source == "GoogleScholar"


def test_merge_makes_study_primary_if_found_in_any_primary_source():
    # Same DOI: a supplementary Google Scholar hit + a primary PubMed hit → the
    # merged study is PRIMARY (supplementary only if every copy is supplementary).
    gs = Citation(id_type=IdType.DOI, id="10.1/x", source="GoogleScholar",
                  title="t", doi="10.1/x", supplementary=True)
    pm = Citation(id_type=IdType.PMID, id="123", source="pubmed",
                  title="t", doi="10.1/x", supplementary=False)
    for order in ([gs, pm], [pm, gs]):   # order-independent
        merged = merge_dedup(order)
        assert len(merged) == 1 and merged[0].supplementary is False


def test_merge_keeps_supplementary_if_all_copies_supplementary():
    a = Citation(id_type=IdType.DOI, id="10.2/y", source="GoogleScholar",
                 title="t2", doi="10.2/y", supplementary=True)
    b = Citation(id_type=IdType.DOI, id="10.2/y", source="handsearch",
                 title="t2", doi="10.2/y", supplementary=True)
    merged = merge_dedup([a, b])
    assert len(merged) == 1 and merged[0].supplementary is True


def test_legacy_flat_exports_detected_vertical_inbox_not(tmp_path):
    # Old flat layout: base/Embase/x.ris (export file directly) -> flagged.
    (tmp_path / "Embase").mkdir()
    (tmp_path / "Embase" / "x.ris").write_text(_EMBASE_RIS, encoding="utf-8")
    # New per-vertical layout: base/<vertical>/<db>/ (only sub-folders) -> NOT flagged.
    (tmp_path / "antithrombin-preeclampsia" / "CENTRAL").mkdir(parents=True)
    (tmp_path / "antithrombin-preeclampsia" / "CENTRAL" / "c.ris").write_text(_EMBASE_RIS, encoding="utf-8")
    assert legacy_flat_exports(base=tmp_path) == ["Embase"]


def test_imported_connectors_readonly_when_missing(tmp_path):
    # A non-existent inbox yields no connectors and creates nothing (side-effect-free).
    missing = tmp_path / "nope"
    assert imported_connectors(base=missing) == []
    assert not missing.exists()


def test_imported_connectors_picks_up_populated_folders(tmp_path):
    (tmp_path / "Embase").mkdir()
    (tmp_path / "Embase" / "e.ris").write_text(_EMBASE_RIS, encoding="utf-8")
    (tmp_path / "GoogleScholar").mkdir()
    (tmp_path / "GoogleScholar" / "g.csv").write_text(_GS_CSV, encoding="utf-8")
    (tmp_path / "Empty").mkdir()  # no files -> ignored
    conns = imported_connectors(base=tmp_path)
    assert sorted(c.source for c in conns) == ["Embase", "GoogleScholar"]
    res = asyncio.run(next(c for c in conns if c.source == "GoogleScholar").search("", {}))
    assert res.citations and all(c.supplementary for c in res.citations)
