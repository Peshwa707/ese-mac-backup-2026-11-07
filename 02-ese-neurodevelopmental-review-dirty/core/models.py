"""Shared Pydantic contracts for the engine.

These models are the stable seam between the pipeline stages and the API. They
are also used directly as structured-output targets for ``messages.parse``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class IdType(str, Enum):
    PMID = "PMID"
    NCT = "NCT"
    DOI = "DOI"
    URL = "URL"  # for sources without a stable scholarly id (e.g. drug labels)


class EvidenceGrade(str, Enum):
    """Generic study-type tiers, highest to lowest. Overridable per vertical."""

    SYSTEMATIC_REVIEW = "systematic_review"
    RCT = "rct"
    COHORT = "cohort"
    CASE_CONTROL = "case_control"
    CASE_SERIES = "case_series"
    CASE_REPORT = "case_report"
    GUIDELINE = "guideline"
    LABEL = "label"
    UNKNOWN = "unknown"


class Citation(BaseModel):
    id_type: IdType
    id: str
    source: str
    title: str = ""
    url: str = ""
    snippet: str = ""
    study_type: Optional[str] = None
    year: Optional[int] = None
    verified: bool = False
    # Cross-source fields (multi-database): a study found in several databases is
    # merged into one canonical Citation carrying all its identifiers.
    doi: str = ""
    extra_ids: dict[str, str] = Field(default_factory=dict)   # e.g. {"PMID": "...", "NCT": "..."}
    provenance: str = ""        # for imported records: the export file it came from
    verification: str = "unverified"  # "live" | "attested" | "unverified"
    # True for supplementary sources (Google Scholar, hand-searching, citation
    # chasing) that are NOT reproducible systematic database searches (Cochrane Ch 4
    # treats Google Scholar as supplementary, not a database). The flag is plumbed and
    # merge-correct (a study found in any primary DB is primary); CONSUMING it to split
    # the PRISMA flow / "databases searched" table is a pending reporting step.
    supplementary: bool = False

    @field_validator("url")
    @classmethod
    def _safe_url_scheme(cls, v: str) -> str:
        """Reject non-http(s) URLs so a malicious ``javascript:`` payload can't
        ride through into an HTML export as a clickable XSS vector. Empty is OK
        (many citations carry no URL)."""
        if not v:
            return v
        scheme = v.split(":", 1)[0].lower().strip() if ":" in v else ""
        if scheme and scheme not in ("http", "https"):
            raise ValueError(f"unsafe citation URL scheme: {scheme!r}")
        return v

    @property
    def key(self) -> str:
        """Normalized cross-source dedup key."""
        return f"{self.id_type.value}:{self.id.strip().upper()}"


class RetrievalResult(BaseModel):
    source: str
    query: str = ""
    citations: list[Citation] = Field(default_factory=list)
    available: bool = True
    note: str = ""


class DeidReport(BaseModel):
    redacted_text: str
    redactions: list[str] = Field(default_factory=list)
    clean: bool = True


class PatientCase(BaseModel):
    raw_text: str
    deid: Optional[DeidReport] = None
    structured: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    statement: str
    citations: list[Citation] = Field(default_factory=list)
    quote: str = ""
    evidence_grade: EvidenceGrade = EvidenceGrade.UNKNOWN
    confidence: float = 0.0
    conflicts_with: list[int] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class ClaimCheck(BaseModel):
    finding_index: int
    supported: bool
    notes: str = ""


class SourceStatus(BaseModel):
    source: str
    available: bool
    note: str = ""


class Provenance(BaseModel):
    report_id: str
    queries: list[str] = Field(default_factory=list)
    raw_results: list[RetrievalResult] = Field(default_factory=list)
    verification_trail: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    # Which model/backend served which pipeline stage at runtime (per-stage AI
    # provenance for the RAISE / PRISMA-trAIce statement). Each entry:
    # {stage, model, backend, n_calls}.
    stage_models: list[dict[str, Any]] = Field(default_factory=list)


class Report(BaseModel):
    id: str
    vertical: str
    structured_case: dict[str, Any] = Field(default_factory=dict)
    decision_support: list[Finding] = Field(default_factory=list)
    manuscript: Optional[dict[str, str]] = None
    sources_status: list[SourceStatus] = Field(default_factory=list)
    provenance_id: str = ""
    disclaimer: str = ""
    empty_reason: str = ""
