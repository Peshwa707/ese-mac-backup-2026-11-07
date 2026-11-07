"""Pydantic contracts for the systematic-review slice.

These are the structured-output targets for the screening and extraction LLM
stages and the shape of the rendered artifacts. They are deliberately small and
explicit so a human Reviewer 2 can diff AI output against the source.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ScreenDecision(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNCERTAIN = "uncertain"  # needs full-text / human adjudication


class ScreeningResult(BaseModel):
    """One Reviewer-1 title/abstract decision (protocol Step 4)."""

    pmid: str
    decision: ScreenDecision
    reason: str = Field(description="Brief justification tied to an eligibility criterion.")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in the decision.")

    @field_validator("confidence", mode="before")
    @classmethod
    def _rescale_confidence(cls, v):
        # Local models honor JSON types but not value bounds; they often emit
        # confidence on a 0-10 or 0-100 scale ("9" / "95"). Rescale to 0-1 and clamp
        # so a sound decision isn't rejected (and forced to cloud) over a scale slip.
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.0
        if x > 1.0:
            x = x / 100.0 if x > 10.0 else x / 10.0
        return min(max(x, 0.0), 1.0)

    modality: str = Field(
        default="",
        description="Subgroup / treatment-arm label for stratified synthesis, as "
        "stated in the record (e.g. the intervention class). '' if the review is "
        "not stratified or the arm is unclear.",
    )
    study_design: str = Field(
        default="unclear",
        description="RCT | single-arm | cohort | registry | review | other.",
    )


class EnsembleScreeningResult(ScreeningResult):
    """A screening decision aggregated from k self-consistency samples.

    The extra fields are computed post-hoc by ``aggregate_ensemble`` — they are NOT
    LLM output, so they are deliberately kept OFF the base ``ScreeningResult`` (which
    is the structured-output schema the model fills and the default artifact shape).
    A single-pass (k=1) screen returns a plain ``ScreeningResult``, so its schema and
    serialized JSON are unchanged; only aggregated (k>1) records carry these fields.
    """

    ensemble_k: int = Field(
        default=1,
        description="Self-consistency samples aggregated into this decision.",
    )
    include_votes: Optional[int] = Field(
        default=None,
        description="INCLUDE votes among the k samples.",
    )


class ScreeningBatch(BaseModel):
    """Batched screening output (one LLM call covers several records)."""

    results: list[ScreeningResult] = Field(default_factory=list)


class ExtractedValue(BaseModel):
    """One extracted data-dictionary cell, grounded or explicitly NR."""

    field: str
    group: str = Field(description="identity | population | efficacy | safety")
    value: str = Field(description="Verbatim/normalized value, 'NR', or 'ESTIMATED: ...'.")
    flag: str = Field(default="", description="'' | 'NR' | 'ESTIMATED'")
    quote: str = Field(default="", description="Verbatim abstract snippet supporting a non-NR value.")

    @field_validator("value", "flag", "quote", "group", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        # Agents sometimes emit null (empty quote/flag) or a list; coerce to str.
        if v is None:
            return ""
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return v


class ExtractedStudy(BaseModel):
    """Abstract-level extraction for one included study (protocol Step 6)."""

    pmid: str
    study_name: str = ""
    modality: str = ""
    data_cut: str = ""
    values: list[ExtractedValue] = Field(default_factory=list)
    extraction_notes: str = ""
    # Trial-level dedup (optional): set when reports are grouped into one trial.
    trial_id: str = ""
    key_source: str = ""              # nct | acronym | singleton
    member_pmids: list[str] = Field(default_factory=list)

    @field_validator("study_name", "modality", "data_cut", "extraction_notes", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return v


class RoBDomain(BaseModel):
    """One risk-of-bias domain judgment (protocol Step 7)."""

    key: str
    label: str = ""
    judgment: str
    rationale: str = ""
    quote: str = ""

    @field_validator("label", "rationale", "quote", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        return "" if v is None else (" ".join(map(str, v)) if isinstance(v, list) else v)


class RoBAssessment(BaseModel):
    pmid: str
    tool: str
    study_design: str = ""
    domains: list[RoBDomain] = Field(default_factory=list)
    overall: str = ""


class GradeRow(BaseModel):
    """GRADE certainty for one outcome (protocol Step 9)."""

    outcome: str
    field: str = ""
    n_studies: int = 0
    n_rct: int = 0
    start: str = ""
    downgrades: list[str] = Field(default_factory=list)  # "domain: reason"
    deferred: list[str] = Field(default_factory=list)
    certainty: str = ""


# --- Aggregated run bundle (rendered + serialized) -------------------------

class PrismaCounts(BaseModel):
    """Counts that populate the PRISMA 2020 flow diagram (protocol Step 5)."""

    identified_pubmed: int = 0
    identified_other_sources: int = 0
    after_dedup: int = 0
    id_unverified_removed: int = 0
    prescreen_excluded: int = 0       # removed by publication type before screening (WP3)
    screened: int = 0                 # records actually screened by title/abstract (post-prescreen)
    excluded_screening: int = 0
    included: int = 0                 # reports (publications) passing screening
    studies_included: int = 0         # distinct studies/trials (= included unless trial-deduped)
    uncertain: int = 0


class ReviewerSignoff(BaseModel):
    """Human Reviewer-2 sign-off that unlocks final conclusions / GRADE certainty /
    recommendations for rendering. Without it the engine renders those as DRAFT."""

    reviewer: str                       # name or initials of the human reviewer
    timestamp: str = ""                 # ISO-8601; set when the sign-off is recorded
    scope: str = "conclusions"          # what was approved (conclusions by default)
    note: str = ""


class SRRun(BaseModel):
    """Full Milestone-1 run result — serialized to JSON and rendered."""

    run_id: str
    review_title: str
    pubmed_query: str
    counts: PrismaCounts
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    screening: list[ScreeningResult] = Field(default_factory=list)
    extractions: list[ExtractedStudy] = Field(default_factory=list)
    named_trial_capture: dict[str, bool] = Field(default_factory=dict)
    seed_verification: dict[str, bool] = Field(default_factory=dict)
    per_source: dict[str, int] = Field(default_factory=dict)
    sources_executed: list[str] = Field(default_factory=list)
    prescreen: dict = Field(default_factory=dict)   # publication-type pre-screen report (WP3)
    sentinel: dict = Field(default_factory=dict)    # named-trial positive-control report (WP4)
    n_attested: int = 0           # provenance-attested (imported) records included
    limitations: list[str] = Field(default_factory=list)
    provenance_id: str = ""
    disclaimer: str = ""
    # Human Reviewer-2 sign-off. None => conclusions/GRADE certainty are LOCKED and
    # render as "DRAFT — awaiting Reviewer 2" (the computed values stay in the data).
    reviewer_signoff: Optional["ReviewerSignoff"] = None
