"""WP-M3.2 — novelty_check front-end stage (verified-PMID, no fabrication)."""
import json

from core.sr.orchestrator import frontend
from core.sr.orchestrator.frontend import NoveltyAssessment, PicoDraft
from core.sr.orchestrator.stages import RunContext


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _write_pico(tmp_path):
    (tmp_path / "pico.json").write_text(PicoDraft(
        population="RRMM 1-3 lines", intervention="BCMA CAR-T",
        comparator="bispecific", outcomes=["PFS"]).model_dump_json())


def test_novelty_report_only_verified_pmids(tmp_path, monkeypatch):
    _write_pico(tmp_path)

    async def _verified(query, **k):
        return [{"pmid": "39551604", "title": "A prior SR of BCMA therapies"}]

    monkeypatch.setattr(frontend, "_verified_prior_reviews", _verified)
    monkeypatch.setattr(frontend, "gateway_parse",
                        lambda *a, **k: NoveltyAssessment(duplication="partial",
                                                          gap_statement="early-relapse subgroup unaddressed",
                                                          recommendation="proceed"))
    res = frontend.novelty_check_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    rep = json.loads((tmp_path / "novelty_report.json").read_text())
    assert rep["n_prior_reviews"] == 1
    assert rep["prior_reviews"][0]["pmid"] == "39551604"
    assert all(r["verified"] is True for r in rep["prior_reviews"])    # no unverified PMID asserted
    assert rep["duplication"] == "partial" and rep["recommendation"] == "proceed"
    assert res.summary["duplication"] == "partial"


def test_novelty_handles_no_prior_reviews(tmp_path, monkeypatch):
    _write_pico(tmp_path)

    async def _none(query, **k):
        return []

    monkeypatch.setattr(frontend, "_verified_prior_reviews", _none)
    monkeypatch.setattr(frontend, "gateway_parse",
                        lambda *a, **k: NoveltyAssessment(duplication="none",
                                                          gap_statement="novel question",
                                                          recommendation="proceed"))
    res = frontend.novelty_check_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    rep = json.loads((tmp_path / "novelty_report.json").read_text())
    assert rep["n_prior_reviews"] == 0 and rep["prior_reviews"] == []
    assert rep["duplication"] == "none"


def test_verified_prior_reviews_drops_unverified(monkeypatch):
    # the helper must return only PMIDs that verify_bulk resolves
    from core.models import Citation, IdType, RetrievalResult

    class _PM:
        async def search(self, q, p):
            return RetrievalResult(source="pubmed", query=q, citations=[
                Citation(id_type=IdType.PMID, id="111", source="pubmed", title="real"),
                Citation(id_type=IdType.PMID, id="999", source="pubmed", title="ghost")])

        async def verify_bulk(self, ids):
            return {"111"}                       # 999 does not resolve

        async def aclose(self):
            pass

    import asyncio
    out = asyncio.run(frontend._verified_prior_reviews("q", connector=_PM()))
    assert [r["pmid"] for r in out] == ["111"]   # ghost dropped (no fabrication)


def test_verified_prior_reviews_fails_closed_on_unavailable_search():
    # a PubMed outage (available=False) must NOT read as "no prior reviews"
    import asyncio
    from core.models import RetrievalResult
    import pytest

    class _Down:
        async def search(self, q, p):
            return RetrievalResult(source="pubmed", query=q, citations=[], available=False)

        async def verify_bulk(self, ids):
            return set()

        async def aclose(self):
            pass

    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(frontend._verified_prior_reviews("q", connector=_Down()))


def test_verified_prior_reviews_fails_closed_when_verifier_down():
    # search succeeds with records, but verify_bulk returns empty (verifier outage)
    import asyncio
    import pytest
    from core.models import Citation, IdType, RetrievalResult

    class _VerDown:
        async def search(self, q, p):
            return RetrievalResult(source="pubmed", query=q, available=True, citations=[
                Citation(id_type=IdType.PMID, id="111", source="pubmed", title="real")])

        async def verify_bulk(self, ids):
            return set()                         # fails soft -> empty

        async def aclose(self):
            pass

    with pytest.raises(RuntimeError, match="verification unavailable"):
        asyncio.run(frontend._verified_prior_reviews("q", connector=_VerDown()))
