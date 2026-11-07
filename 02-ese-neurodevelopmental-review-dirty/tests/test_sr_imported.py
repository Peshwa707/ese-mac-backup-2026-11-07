"""Tests for the generic import connector (RIS/NBIB/CSV) + attested verification."""

from __future__ import annotations

import httpx
import respx

from core.connectors.imported import (
    ImportedConnector,
    imported_connectors,
    parse_csv,
    parse_nbib,
    parse_ris,
    to_citation,
)
from core.models import IdType

_RIS = """TY  - JOUR
TI  - A CENTRAL trial of cilta-cel
AB  - ORR was 85%.
PY  - 2024
DO  - 10.9/central1
ER  -
"""

_NBIB = """PMID- 12345678
TI  - An Embase study of teclistamab
AB  - Some abstract.
DP  - 2023 Jun
AID - 10.8/embase1 [doi]
"""

_CSV = "Title,DOI,PMID,Year,Abstract\nA WoS study,10.7/wos1,,2022,WoS abstract\n"


def test_parse_ris():
    recs = parse_ris(_RIS)
    assert len(recs) == 1 and recs[0]["doi"] == "10.9/central1" and recs[0]["year"] == "2024"
    c = to_citation(recs[0], "CENTRAL", "central.ris")
    assert c.id_type == IdType.DOI and c.source == "CENTRAL" and c.provenance == "central.ris"


def test_parse_nbib():
    recs = parse_nbib(_NBIB)
    assert recs[0]["pmid"] == "12345678" and recs[0]["doi"] == "10.8/embase1"
    c = to_citation(recs[0], "Embase", "embase.nbib")
    assert c.id_type == IdType.PMID and c.id == "12345678"


def test_parse_csv():
    recs = parse_csv(_CSV)
    assert recs[0]["title"] == "A WoS study" and recs[0]["doi"] == "10.7/wos1"


async def test_imported_connector_reads_folder(tmp_path):
    (tmp_path / "central.ris").write_text(_RIS)
    (tmp_path / "studies.csv").write_text(_CSV)
    conn = ImportedConnector("CENTRAL", tmp_path)
    res = await conn.search("ignored", {})
    assert len(res.citations) == 2 and all(c.source == "CENTRAL" for c in res.citations)


def test_imported_connectors_discovers_databases(tmp_path):
    (tmp_path / "CENTRAL").mkdir()
    (tmp_path / "CENTRAL" / "a.ris").write_text(_RIS)
    (tmp_path / "Empty").mkdir()
    conns = imported_connectors(tmp_path)
    assert [c.source for c in conns] == ["CENTRAL"]   # Empty folder skipped


@respx.mock
async def test_attested_record_included_without_live_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCO_PROVENANCE_DIR", str(tmp_path / "prov"))
    # No PubMed/Crossref resolution for the imported DOI -> falls to attested.
    respx.get(url__regex=r"esummary\.fcgi").mock(return_value=httpx.Response(200, json={"result": {"uids": []}}))
    respx.get(url__regex=r"api\.crossref\.org").mock(return_value=httpx.Response(404))
    (tmp_path / "central.ris").write_text(_RIS)
    from core.sr.pipeline import gather_corpus
    corpus = await gather_corpus(sources=[ImportedConnector("CENTRAL", tmp_path)])
    assert len(corpus.verified) == 1
    assert corpus.verified[0].verification == "attested" and corpus.verified[0].provenance == "central.ris"
