"""Test that ClinicalTrials.gov NCT records verify in gather_corpus."""

from __future__ import annotations

import httpx
import respx

from core.connectors.clinicaltrials import ClinicalTrialsConnector
from core.models import Citation, IdType, RetrievalResult


class _FakeCT(ClinicalTrialsConnector):
    source = "clinicaltrials"

    async def search(self, query, structured):
        return RetrievalResult(source="clinicaltrials", query=query, citations=[
            Citation(id_type=IdType.NCT, id="NCT04999999", source="clinicaltrials",
                     title="A BCMA CAR-T trial")])

    async def verify(self, citation):
        return citation.id_type == IdType.NCT     # CT.gov resolves the NCT


@respx.mock
async def test_nct_record_verified_in_gather(monkeypatch, tmp_path):
    monkeypatch.setenv("ONCO_PROVENANCE_DIR", str(tmp_path))
    respx.get(url__regex=r"esummary\.fcgi").mock(return_value=httpx.Response(200, json={"result": {"uids": []}}))
    from core.sr.pipeline import gather_corpus
    corpus = await gather_corpus(sources=[_FakeCT()])
    assert len(corpus.verified) == 1
    c = corpus.verified[0]
    assert c.id_type == IdType.NCT and c.verification == "live"
    assert corpus.per_source.get("clinicaltrials") == 1
