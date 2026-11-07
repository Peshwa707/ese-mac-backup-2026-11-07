from core.grounding.verify import apply_cite_or_omit, quote_is_anchored
from core.models import Citation, Finding, IdType


def _cite(cid, snippet=""):
    return Citation(id_type=IdType.PMID, id=cid, source="pubmed", snippet=snippet)


def test_quote_anchoring():
    cites = [_cite("1", "Osimertinib improved progression-free survival markedly.")]
    assert quote_is_anchored("improved progression-free survival", cites)
    assert not quote_is_anchored("cured every patient overnight", cites)
    assert not quote_is_anchored("short", cites)  # below min length


def test_cite_or_omit_drops_unverified_and_remaps_conflicts():
    snippet = "Osimertinib improved progression-free survival markedly."
    f0 = Finding(statement="kept", citations=[_cite("1", snippet)], quote="improved progression-free survival", conflicts_with=[2])
    f1 = Finding(statement="dropped — unverified", citations=[_cite("99")])
    f2 = Finding(statement="kept2", citations=[_cite("2", snippet)], quote="improved progression-free survival", conflicts_with=[0])

    kept, dropped = apply_cite_or_omit([f0, f1, f2], verified_keys={"PMID:1", "PMID:2"})
    assert [f.statement for f in kept] == ["kept", "kept2"]
    assert dropped[0]["reason"] == "no_verified_citation"
    # f0 conflicted with old index 2 (f2), now re-indexed to 1.
    assert kept[0].conflicts_with == [1]


def test_unanchored_quote_is_dropped_as_gate():
    # Quote-anchoring is a GATE: an unanchored quote drops the finding entirely
    # and is recorded as a rejection (it is no longer merely flagged-and-kept).
    f = Finding(statement="x", citations=[_cite("1", "abstract text here")], quote="totally unrelated claim text")
    kept, dropped = apply_cite_or_omit([f], verified_keys={"PMID:1"})
    assert kept == []
    assert dropped and dropped[0]["reason"] == "quote_not_anchored"


def test_empty_quote_is_dropped():
    f = Finding(statement="x", citations=[_cite("1", "abstract text here")], quote="")
    kept, dropped = apply_cite_or_omit([f], verified_keys={"PMID:1"})
    assert kept == []
    assert dropped[0]["reason"] == "quote_not_anchored"


def test_quote_gate_remaps_conflicts_after_drop():
    snippet = "Osimertinib improved progression-free survival markedly."
    # f0 anchored & kept; f1 unanchored & dropped by the gate; f2 anchored & kept.
    f0 = Finding(statement="kept", citations=[_cite("1", snippet)], quote="improved progression-free survival", conflicts_with=[2])
    f1 = Finding(statement="dropped — bad quote", citations=[_cite("2", snippet)], quote="not in any snippet")
    f2 = Finding(statement="kept2", citations=[_cite("3", snippet)], quote="improved progression-free survival", conflicts_with=[0])

    kept, dropped = apply_cite_or_omit([f0, f1, f2], verified_keys={"PMID:1", "PMID:2", "PMID:3"})
    assert [f.statement for f in kept] == ["kept", "kept2"]
    assert any(d["reason"] == "quote_not_anchored" for d in dropped)
    # f0 conflicted with old index 2 (f2) -> now re-indexed to 1.
    assert kept[0].conflicts_with == [1]
