"""Manuscript cite-or-omit enforcement (code-review AH findings, 2026-06-04).

The manuscript audit must DROP ungrounded sentences (not merely flag them), the
returned manuscript must NOT carry an internal ``flags`` key (no leak into the
JSON export contract), and the inline-citation regex must tolerate a space after
the colon and DOI-style keys.
"""

from __future__ import annotations

import core.synthesis.synthesize as synthesize
from core.config.vertical import load_vertical
from core.models import Citation, Finding, IdType

_VERTICAL = load_vertical("oncology")


def _finding(stmt: str, cid: str = "111") -> Finding:
    return Finding(
        statement=stmt,
        citations=[Citation(id_type=IdType.PMID, id=cid, source="pubmed", snippet="s")],
        quote="snippet",
    )


def _run(monkeypatch, manuscript_fields: dict, findings):
    monkeypatch.setattr(
        synthesize.llm, "parse", lambda *a, **k: synthesize._Manuscript(**manuscript_fields)
    )
    return synthesize.generate_manuscript({}, findings, _VERTICAL)


def test_ungrounded_sentence_is_dropped_not_flagged(monkeypatch):
    findings = [_finding("Osimertinib improves PFS.", "111")]
    fields = {
        "discussion": (
            "Osimertinib improves outcomes [PMID:111]. "
            "It also reverses aging in all patients."  # ungrounded → must drop
        )
    }
    ms, flags = _run(monkeypatch, fields, findings)
    assert "reverses aging" not in ms["discussion"], "ungrounded sentence shipped"
    assert "[PMID:111]" in ms["discussion"], "grounded sentence wrongly dropped"
    assert any("reverses aging" in f for f in flags), "dropped sentence not audited"


def test_manuscript_has_no_flags_key(monkeypatch):
    ms, flags = _run(monkeypatch, {"discussion": "x"}, [_finding("a")])
    assert "flags" not in ms, "internal audit text leaked into manuscript/export"
    assert isinstance(flags, list)


def test_ref_regex_tolerates_space_and_doi(monkeypatch):
    # A DOI-keyed citation; prose cites it with a space after the colon.
    doi_finding = Finding(
        statement="claim",
        citations=[Citation(id_type=IdType.DOI, id="10.1001/jama.2020", source="pubmed", snippet="s")],
        quote="q",
    )
    fields = {"discussion": "The trial showed benefit [doi: 10.1001/jama.2020]."}
    ms, flags = _run(monkeypatch, fields, [doi_finding])
    assert "benefit" in ms["discussion"], "valid DOI/spaced citation wrongly dropped"
    assert flags == [], f"unexpected flags: {flags}"


def test_introduction_is_now_audited(monkeypatch):
    # A clinical claim in the introduction with no citation must be dropped.
    fields = {"introduction": "EGFR mutations occur in roughly 15% of cases."}
    ms, flags = _run(monkeypatch, fields, [_finding("a")])
    assert ms["introduction"] == "", "uncited introduction claim was not dropped"
    assert any(f.startswith("introduction:") for f in flags)


def test_fabricated_citation_sentence_dropped(monkeypatch):
    findings = [_finding("real", "111")]
    fields = {"discussion": "A bold claim [PMID:999999] not in the pool."}
    ms, flags = _run(monkeypatch, fields, findings)
    assert ms["discussion"] == "", "sentence citing unverified key was kept"
    assert any("unverified key" in f for f in flags)
