"""Evidence grading by study-type hierarchy + recency.

Study-type tiers are generic (not domain-specific) with a config-overridable
keyword map. Each finding's grade is the strongest grade among its citations;
confidence blends grade rank with recency and citation count.
"""

from __future__ import annotations

import datetime
from typing import Any

from core.models import Citation, EvidenceGrade, Finding

# Higher rank = stronger evidence.
_RANK: dict[EvidenceGrade, int] = {
    EvidenceGrade.SYSTEMATIC_REVIEW: 6,
    EvidenceGrade.RCT: 5,
    EvidenceGrade.GUIDELINE: 5,
    EvidenceGrade.COHORT: 4,
    EvidenceGrade.CASE_CONTROL: 3,
    EvidenceGrade.CASE_SERIES: 2,
    EvidenceGrade.LABEL: 2,
    EvidenceGrade.CASE_REPORT: 1,
    EvidenceGrade.UNKNOWN: 0,
}

# Substring keywords (lowercased) -> grade. First match wins, ordered strongest.
_KEYWORDS: list[tuple[str, EvidenceGrade]] = [
    ("systematic review", EvidenceGrade.SYSTEMATIC_REVIEW),
    ("meta-analysis", EvidenceGrade.SYSTEMATIC_REVIEW),
    ("randomized", EvidenceGrade.RCT),
    ("phase 3", EvidenceGrade.RCT),
    ("phase iii", EvidenceGrade.RCT),
    ("guideline", EvidenceGrade.GUIDELINE),
    ("cohort", EvidenceGrade.COHORT),
    ("case-control", EvidenceGrade.CASE_CONTROL),
    ("case series", EvidenceGrade.CASE_SERIES),
    ("label", EvidenceGrade.LABEL),
    ("case report", EvidenceGrade.CASE_REPORT),
]


def grade_citation(citation: Citation, overrides: dict[str, str] | None = None) -> EvidenceGrade:
    haystack = " ".join(
        x for x in (citation.study_type or "", citation.title, citation.source) if x
    ).lower()
    keywords = list(_KEYWORDS)
    if overrides:
        keywords = [(k.lower(), EvidenceGrade(v)) for k, v in overrides.items()] + keywords
    for kw, grade in keywords:
        if kw in haystack:
            return grade
    return EvidenceGrade.UNKNOWN


def _confidence(grade: EvidenceGrade, citations: list[Citation]) -> float:
    rank = _RANK.get(grade, 0) / 6.0
    n_factor = min(len(citations), 3) / 3.0
    year = datetime.date.today().year
    recencies = [1.0 - min(max(year - (c.year or year - 20), 0), 20) / 20.0 for c in citations]
    recency = max(recencies) if recencies else 0.0
    return round(0.6 * rank + 0.25 * recency + 0.15 * n_factor, 3)


def grade_findings(findings: list[Finding], config: dict[str, Any] | None = None) -> list[Finding]:
    overrides = (config or {}).get("study_type_overrides")
    for f in findings:
        grades = [grade_citation(c, overrides) for c in f.citations]
        best = max(grades, key=lambda g: _RANK.get(g, 0)) if grades else EvidenceGrade.UNKNOWN
        f.evidence_grade = best
        for c, g in zip(f.citations, grades):
            if c.study_type is None:
                c.study_type = g.value
        f.confidence = _confidence(best, f.citations)
        if _RANK.get(best, 0) <= 1:
            f.flags.append("low_evidence")
    return findings
