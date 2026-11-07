"""Tests for deterministic GRADE certainty computation."""

from __future__ import annotations

from core.sr.grade import grade_outcomes
from core.sr.models import (
    ExtractedStudy,
    ExtractedValue,
    RoBAssessment,
    ScreenDecision,
    ScreeningResult,
)


def _ext(pmid, orr_real):
    v = ExtractedValue(field="orr", group="efficacy",
                       value="84%" if orr_real else "NR", flag="" if orr_real else "NR")
    return ExtractedStudy(pmid=pmid, values=[v])


def test_grade_applies_indirectness_and_imprecision():
    extractions = [_ext("A", True), _ext("B", False)]
    screening = [ScreeningResult(pmid="A", decision=ScreenDecision.INCLUDE, reason="x",
                                 confidence=0.9, study_design="RCT"),
                 ScreeningResult(pmid="B", decision=ScreenDecision.INCLUDE, reason="x",
                                 confidence=0.9, study_design="cohort")]
    rob = [RoBAssessment(pmid="A", tool="rob2", overall="Low")]
    rows = grade_outcomes(extractions, screening, rob)
    orr = next(r for r in rows if r.field == "orr")
    assert orr.n_studies == 1 and orr.n_rct == 1 and orr.start == "High"
    assert any("Indirectness" in d for d in orr.downgrades)
    assert any("Imprecision" in d for d in orr.downgrades)   # n=1 < 3
    assert orr.certainty == "Low"                            # High -2 = Low


def test_grade_no_data_outcome():
    rows = grade_outcomes([_ext("A", True)],
                          [ScreeningResult(pmid="A", decision=ScreenDecision.INCLUDE,
                                           reason="x", confidence=0.9, study_design="RCT")],
                          [])
    os_row = next(r for r in rows if r.field == "median_os")
    assert os_row.n_studies == 0 and os_row.certainty == "No data"


def test_grade_risk_of_bias_downgrade():
    # 3 observational studies report orr; 2/3 serious -> RoB downgrade; start Low.
    extractions = [_ext("A", True), _ext("B", True), _ext("C", True)]
    screening = [ScreeningResult(pmid=p, decision=ScreenDecision.INCLUDE, reason="x",
                                 confidence=0.9, study_design="cohort") for p in ("A", "B", "C")]
    rob = [RoBAssessment(pmid="A", tool="robins_i", overall="Serious"),
           RoBAssessment(pmid="B", tool="robins_i", overall="Serious"),
           RoBAssessment(pmid="C", tool="robins_i", overall="Low")]
    orr = next(r for r in grade_outcomes(extractions, screening, rob) if r.field == "orr")
    assert orr.start == "Low" and any("Risk of bias" in d for d in orr.downgrades)
    assert orr.certainty == "Very Low"   # Low, -indirectness, -RoB
