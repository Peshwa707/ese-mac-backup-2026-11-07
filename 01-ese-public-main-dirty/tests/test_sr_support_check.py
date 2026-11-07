"""Hardened per-claim support check: k-of-n multi-vote + per-citation scoped pool (Piece 3)."""
from __future__ import annotations

from core.models import ClaimCheck


def _stub_votes(monkeypatch, ballots):
    """Route citation_support parse calls to a fixed sequence of supported/raise ballots."""
    seq = iter(ballots)

    def _parse(schema, system, user, *, stage="unknown", **kw):
        assert stage == "citation_support"
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return ClaimCheck(finding_index=0, supported=v, notes="n")

    monkeypatch.setattr("core.llm.client.parse", _parse)


def test_multi_vote_supported_on_majority(monkeypatch):
    from core.tools.builtin import _claim_support_check
    _stub_votes(monkeypatch, [True, False, True])   # 2 of 3
    res = _claim_support_check("claim", "sources")
    assert res["supported"] is True
    assert res["n_supported"] == 2 and res["votes"] == [True, False, True]
    assert res["n_votes"] == 3 and res["min_required"] == 2


def test_multi_vote_unsupported_on_minority(monkeypatch):
    from core.tools.builtin import _claim_support_check
    _stub_votes(monkeypatch, [True, False, False])  # 1 of 3
    res = _claim_support_check("claim", "sources")
    assert res["supported"] is False and res["n_supported"] == 1


def test_errored_ballot_counts_as_unsupported(monkeypatch):
    from core.tools.builtin import _claim_support_check
    _stub_votes(monkeypatch, [True, RuntimeError("boom"), RuntimeError("boom")])
    res = _claim_support_check("claim", "sources")
    assert res["votes"] == [True, False, False] and res["supported"] is False


def test_citation_support_stage_is_cloud_only(monkeypatch):
    from core.llm import client
    monkeypatch.setenv("SR_LLM_PROFILE", "local")
    client.set_forced_backend("local")
    try:
        assert client.backend_for("citation_support") == "anthropic"
    finally:
        client.clear_forced_backend()


def test_scoped_pool_binds_to_cited_studies_only():
    # The support pool is scoped to the CITED study/studies: their abstract + own quotes,
    # NOT the whole corpus and NOT reference titles (a title is not evidence for a claim).
    from core.tools.builtin import _support_sources_for
    records = {"111": {"abstract": "Trial 111 abstract."},
               "222": {"abstract": "Trial 222 abstract."}}
    quotes = {"111": ["the observed ORR was 84%"]}
    pool = _support_sources_for(records, ["111"], quotes)
    assert "Trial 111 abstract." in pool
    assert "the observed ORR was 84%" in pool            # cited study's own quote
    assert "Trial 222 abstract." not in pool             # a NON-cited study is excluded


def test_support_check_binds_claim_to_cited_source(monkeypatch, tmp_path):
    # A claim citing [PMID: 111] must be supported by 111 — not by a different corpus study.
    # Judge votes "supported" iff the SOURCES (not the claim) contain "tripled".
    from core.models import ClaimCheck
    from core.tools import builtin

    def _parse(schema, system, user, *, stage="citation_support", **kw):
        sources = user.split("<sources>", 1)[-1]
        return ClaimCheck(finding_index=0, supported=("tripled" in sources), notes="n")

    monkeypatch.setattr("core.llm.client.parse", _parse)
    records = {"111": {"abstract": "Trial 111 reported stable disease."},        # cited, no support
               "222": {"abstract": "Overall survival tripled in trial 222."}}    # NOT cited, supports
    narrative = {"abstract_results": "Overall survival tripled [PMID: 111].",
                 "abstract_background": "", "abstract_conclusions": "",
                 "introduction": "", "discussion": ""}
    cleaned, dropped, checked, metrics = builtin._filter_narrative_claims(narrative, tmp_path, records)
    assert metrics["dropped_unsupported"] == 1           # dropped: the CITED study lacks support
    assert "Overall survival tripled" not in " ".join(cleaned.values())   # claim did not survive


def test_cited_pmids_handles_lists_and_malformed():
    from core.tools.builtin import _cited_pmids
    assert _cited_pmids("x [PMID: 111]") == ["111"]
    assert _cited_pmids("x [PMID: 111, 222]") == ["111", "222"]   # comma list -> both
    assert _cited_pmids("x [PMID: abc]") == []                    # malformed -> uncited
    assert _cited_pmids("no citation") == []


def test_filter_drops_unsupported_keeps_supported(tmp_path, monkeypatch):
    """One known-supported and one known-unsupported sentence: the unsupported one is
    dropped by the majority vote; the supported one survives."""
    from core.models import ClaimCheck
    from core.tools.builtin import _filter_narrative_claims

    def _parse(schema, system, user, *, stage="unknown", **kw):
        claim = user.split("</claim>")[0]
        supported = "tripled" not in claim   # the fabricated sentence fails; the grounded one passes
        return ClaimCheck(finding_index=0, supported=supported, notes="")

    monkeypatch.setattr("core.llm.client.parse", _parse)
    narrative = {
        "abstract_background": "",
        "abstract_results": "The ORR was 84% [PMID: 111]. Survival tripled overnight [PMID: 111].",
        "abstract_conclusions": "",
        "introduction": "",
        "discussion": "",
    }
    records = {"111": {"abstract": "In this trial the ORR was 84%."}}
    cleaned, dropped, checked, metrics = _filter_narrative_claims(narrative, tmp_path, records)
    assert "84%" in cleaned["abstract_results"]
    assert "tripled" not in cleaned["abstract_results"]
    assert metrics["dropped_unsupported"] == 1 and metrics["kept"] >= 1
    # Provenance carries the per-sentence ballots.
    dropped_check = next(c for c in checked if "tripled" in c["sentence"])
    assert dropped_check["claim_check"]["votes"] == [False, False, False]
