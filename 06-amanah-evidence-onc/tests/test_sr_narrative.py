"""Tests for narrative citation validation, evidence brief, and prose wiring."""

from __future__ import annotations

from core.sr.config import load_sr_config
from core.sr.narrative import (
    build_evidence_brief,
    valid_citation_pmids,
    validate_citations,
)
from tests.test_sr_manuscript import _RECORDS, _synthetic_run


def test_validate_citations_flags_unverified():
    valid = {"111", "39551604"}
    ok, bad = validate_citations("Cite [PMID: 111] and [PMID: 999] and [PMID: 39551604].", valid)
    assert ok == ["111", "39551604"] and bad == ["999"]


def test_valid_citation_pmids_includes_included_and_seeds():
    run = _synthetic_run()                       # includes pmid 111
    valid = valid_citation_pmids(run, ["39551604"])
    assert "111" in valid and "39551604" in valid


def test_evidence_brief_is_grounded():
    run = _synthetic_run()
    brief = build_evidence_brief(run, load_sr_config(), None, [], [], ["39551604"],
                                 included=[{"pmid": "111", "modality": "CAR-T", "title": "A trial"}])
    assert "Included studies: 1" in brief
    assert "[PMID: 111]" in brief and "39551604" in brief


def test_evidence_brief_does_not_leak_myeloma_into_other_verticals():
    # Regression: the brief used to hardcode "CAR-T", "BsAb", "no head-to-head
    # CAR-T vs bispecific RCT", and myeloma prior-review PMIDs for every vertical.
    run = _synthetic_run()
    brief = build_evidence_brief(run, load_sr_config("heart-failure-sglt2"),
                                 None, [], [], [])
    low = brief.lower()
    for term in ("car-t", "bsab", "bcma", "rrmm", "myeloma", "bispecific"):
        assert term not in low, f"leaked {term!r} into a heart-failure brief"
    # HF is a direct head-to-head comparison -> no indirect-comparison caveat.
    assert "indirect comparison" not in low


def test_narrative_prose_fills_components():
    from core.sr.manuscript.assemble import build_components
    from core.sr.manuscript.model import Paragraph

    narrative = {
        "introduction": "Intro prose with rationale.",
        "abstract_background": "BG.",
        "abstract_results": "CAR-T ORR was 85.4%.",
        "abstract_conclusions": "Cautious conclusion.",
        "discussion": "Discussion prose comparing modalities.",
    }
    comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS, narrative=narrative)
    ta = next(c for c in comps if c.key == "00_title_abstract")
    disc = next(c for c in comps if c.key == "09_discussion")
    assert any(isinstance(b, Paragraph) and "85.4%" in b.text for b in ta.blocks)
    assert any(isinstance(b, Paragraph) and "Discussion prose" in b.text for b in disc.blocks)
