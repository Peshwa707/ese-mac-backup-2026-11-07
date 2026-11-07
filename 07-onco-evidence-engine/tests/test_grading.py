from core.models import Citation, EvidenceGrade, Finding, IdType
from core.synthesis.grading import grade_citation, grade_findings


def _cite(study_type, year=2023):
    return Citation(id_type=IdType.PMID, id="1", source="pubmed", study_type=study_type, year=year)


def test_grade_citation_hierarchy():
    assert grade_citation(_cite("Randomized Controlled Trial")) == EvidenceGrade.RCT
    assert grade_citation(_cite("Meta-Analysis")) == EvidenceGrade.SYSTEMATIC_REVIEW
    assert grade_citation(_cite("Case Reports")) == EvidenceGrade.CASE_REPORT
    assert grade_citation(_cite("something unrecognized")) == EvidenceGrade.UNKNOWN


def test_finding_takes_strongest_grade_and_flags_low_evidence():
    strong = Finding(statement="x", citations=[_cite("Randomized Controlled Trial")])
    weak = Finding(statement="y", citations=[_cite("Case Reports", year=2005)])
    graded = grade_findings([strong, weak])
    assert graded[0].evidence_grade == EvidenceGrade.RCT
    assert graded[0].confidence > graded[1].confidence
    assert "low_evidence" in graded[1].flags
