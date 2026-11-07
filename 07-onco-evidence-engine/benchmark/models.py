"""Pydantic contracts for offline benchmark artifacts.

These models describe the future blind benchmark without running the engine or
querying live services. Draft artifacts may be incomplete; sealed answer keys
must carry seal metadata and fully specified targets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


OutcomeFamily = Literal["proportion", "log_effect", "binary", "continuous"]
EffectMeasure = Literal["proportion", "hr", "rr", "or", "rd", "smd", "md"]
ConclusionStance = Literal["effect", "no-effect", "uncertain", "withheld"]
ConcordanceDirection = Literal["above_null", "below_null", "at_null", "na"]
ConcordanceSignificance = Literal["significant", "not_significant", "na"]
CaseStatus = Literal["draft", "sealed"]
CandidateStatus = Literal["candidate", "include", "exclude"]
ScoreClassification = Literal["matched", "mismatched", "missing_engine", "missing_key", "not_scored"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _nonempty(v: str) -> str:
    s = str(v or "").strip()
    if not s:
        raise ValueError("must be non-empty")
    return s


def _finite_float(v: float | None) -> float | None:
    if v is None:
        return None
    x = float(v)
    if not isfinite(x):
        raise ValueError("must be finite")
    return x


class ReviewCitation(BaseModel):
    title: str
    journal: str = ""
    year: int | None = None
    doi: str = ""
    pmid: str = ""
    authors: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _nonempty(v)


class OutcomeSlot(BaseModel):
    field: str
    label: str = ""
    family: OutcomeFamily
    group: str = "ALL"

    @field_validator("field")
    @classmethod
    def _field(cls, v: str) -> str:
        return _nonempty(v)


class BenchmarkCase(BaseModel):
    case_id: str
    citation: ReviewCitation
    vertical_name: str
    intended_run_dir: str
    answer_key_path: str
    protocol_source_path: str = ""
    protocol_spec_path: str = ""
    status: CaseStatus = "draft"
    outcome_slots: list[OutcomeSlot] = Field(default_factory=list)
    seal_digest: str = ""
    seal_signature: str = ""

    @field_validator("case_id", "vertical_name", "intended_run_dir", "answer_key_path")
    @classmethod
    def _required_text(cls, v: str) -> str:
        return _nonempty(v)

    @model_validator(mode="after")
    def _sealed_has_metadata(self):
        if self.status == "sealed" and (not self.seal_digest or not self.seal_signature):
            raise ValueError("sealed benchmark case requires seal_digest and seal_signature")
        return self


class CandidateReview(BaseModel):
    candidate_id: str
    citation: ReviewCitation
    status: CandidateStatus = "candidate"
    reason: str = ""
    protocol_source_path: str = ""
    notes: str = ""

    @field_validator("candidate_id")
    @classmethod
    def _candidate_id(cls, v: str) -> str:
        return _nonempty(v)

    @model_validator(mode="after")
    def _excluded_has_reason(self):
        if self.status == "exclude" and not self.reason.strip():
            raise ValueError("excluded candidate requires reason")
        return self


class SamplingFrame(BaseModel):
    version: str = "phase0"
    generated_at: str = Field(default_factory=utc_now)
    candidates: list[CandidateReview]
    source_note: str = "offline candidate list"

    @field_validator("candidates")
    @classmethod
    def _unique_candidates(cls, v: list[CandidateReview]) -> list[CandidateReview]:
        ids = [c.candidate_id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id values must be unique")
        return v


class OutcomeTarget(BaseModel):
    field: str
    label: str = ""
    family: OutcomeFamily
    group: str = "ALL"
    metric: Literal["prop", "hr", "estimate"] = "estimate"
    effect_measure: EffectMeasure | None = None
    estimate: float | None = None
    ci_lb: float | None = None
    ci_ub: float | None = None
    i2: float | None = None
    tolerance: float = 0.1
    ci_tolerance: float = 0.1

    @field_validator("field")
    @classmethod
    def _field(cls, v: str) -> str:
        return _nonempty(v)

    @field_validator("estimate", "ci_lb", "ci_ub", "i2", "tolerance", "ci_tolerance")
    @classmethod
    def _finite(cls, v):
        return _finite_float(v)

    @model_validator(mode="after")
    def _ci_complete_and_ordered(self):
        has_lb = self.ci_lb is not None
        has_ub = self.ci_ub is not None
        if has_lb != has_ub:
            raise ValueError("CI must provide both ci_lb and ci_ub")
        if has_lb and self.ci_lb > self.ci_ub:
            raise ValueError("ci_lb must be <= ci_ub")
        if self.tolerance < 0 or self.ci_tolerance < 0:
            raise ValueError("tolerances must be non-negative")
        if self.family == "proportion":
            self.metric = "prop"
            self.effect_measure = self.effect_measure or "proportion"
        elif self.family == "log_effect":
            self.metric = "hr"
            self.effect_measure = self.effect_measure or "hr"
        elif self.family == "binary":
            self.metric = "estimate"
            self.effect_measure = self.effect_measure or "rr"
        elif self.family == "continuous":
            self.metric = "estimate"
            self.effect_measure = self.effect_measure or "smd"
        else:
            self.metric = "estimate"
        return self

    @property
    def is_scored(self) -> bool:
        return self.estimate is not None


class AnswerKey(BaseModel):
    case_id: str
    status: CaseStatus = "draft"
    expected_included_pmids: list[str] = Field(default_factory=list)
    expected_included_trial_ids: list[str] = Field(default_factory=list)
    expected_conclusion: ConclusionStance | None = None
    outcomes: list[OutcomeTarget] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    seal_digest: str = ""
    seal_signature: str = ""

    @field_validator("case_id")
    @classmethod
    def _case_id(cls, v: str) -> str:
        return _nonempty(v)

    @field_validator("expected_included_pmids")
    @classmethod
    def _pmids_unique(cls, v: list[str]) -> list[str]:
        pmids = [str(p).strip() for p in v if str(p).strip()]
        if len(pmids) != len(set(pmids)):
            raise ValueError("expected_included_pmids must be unique")
        return pmids

    @field_validator("expected_included_trial_ids")
    @classmethod
    def _trial_ids_unique(cls, v: list[str]) -> list[str]:
        ids = [str(p).strip() for p in v if str(p).strip()]
        if len(ids) != len(set(ids)):
            raise ValueError("expected_included_trial_ids must be unique")
        return ids

    @model_validator(mode="after")
    def _sealed_has_complete_targets(self):
        if self.status == "sealed":
            if not self.seal_digest or not self.seal_signature:
                raise ValueError("sealed answer key requires seal_digest and seal_signature")
            missing = [o.field for o in self.outcomes if not o.is_scored]
            if missing:
                raise ValueError(f"sealed answer key has unfilled outcome targets: {missing}")
        return self


class SelectionMetrics(BaseModel):
    expected_n: int = 0
    engine_n: int = 0
    overlap_n: int = 0
    recall: float | None = None
    precision: float | None = None
    jaccard: float | None = None
    report_expected_n: int = 0
    report_engine_n: int = 0
    report_overlap_n: int = 0
    report_recall: float | None = None
    report_precision: float | None = None
    report_jaccard: float | None = None
    study_expected_n: int = 0
    study_engine_n: int = 0
    study_overlap_n: int = 0
    study_recall: float | None = None
    study_precision: float | None = None
    study_jaccard: float | None = None


class OutcomeScore(BaseModel):
    field: str
    label: str = ""
    family: OutcomeFamily
    group: str = "ALL"
    classification: ScoreClassification
    effect_measure: EffectMeasure | str = ""
    null_value: float | None = None
    expected: float | None = None
    observed: float | None = None
    delta: float | None = None
    expected_ci: list[float] | None = None
    observed_ci: list[float] | None = None
    expected_direction: ConcordanceDirection = "na"
    observed_direction: ConcordanceDirection = "na"
    expected_significance: ConcordanceSignificance = "na"
    observed_significance: ConcordanceSignificance = "na"
    direction_agree: bool | None = None
    significance_agree: bool | None = None
    engine_estimate_in_published_ci: bool | None = None
    published_estimate_in_engine_ci: bool | None = None
    ci_interval_overlap: bool | None = None
    expected_i2: float | None = None
    observed_i2: float | None = None
    i2_delta: float | None = None
    headline_pass: bool | None = None
    note: str = ""


class IntegrityMetrics(BaseModel):
    estimated_value_count: int = 0
    nr_value_count: int = 0
    pooled_n_excluded_total: int = 0


class ConclusionScore(BaseModel):
    expected: ConclusionStance | None = None
    observed: ConclusionStance | None = None
    matched: bool | None = None
    note: str = ""


class ScoreReport(BaseModel):
    case_id: str
    generated_at: str = Field(default_factory=utc_now)
    key_sealed: bool = False
    selection: SelectionMetrics = Field(default_factory=SelectionMetrics)
    integrity: IntegrityMetrics = Field(default_factory=IntegrityMetrics)
    conclusion: ConclusionScore = Field(default_factory=ConclusionScore)
    outcomes: list[OutcomeScore] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @field_validator("case_id")
    @classmethod
    def _score_case_id(cls, v: str) -> str:
        return _nonempty(v)

    @property
    def counts_by_classification(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.classification] = counts.get(outcome.classification, 0) + 1
        return counts


class RunPlan(BaseModel):
    case_id: str
    generated_at: str = Field(default_factory=utc_now)
    commands: list[str]
    expected_artifacts: list[str]
    caveats: list[str] = Field(default_factory=list)


class HarnessStageStatus(BaseModel):
    stage: Literal["screen", "extract"]
    source: Literal["claude", "gpt"]
    request_path: str = ""
    n_batches: int = 0
    expected_outputs: list[str] = Field(default_factory=list)
    missing_outputs: list[str] = Field(default_factory=list)
    valid_items: int = 0
    repair_items: list[str] = Field(default_factory=list)
    status: Literal["missing", "pending", "partial", "complete"] = "missing"


class HarnessStatusReport(BaseModel):
    run_dir: str
    source: Literal["claude", "gpt"]
    stages: list[HarnessStageStatus]
