"""Small, explicit data contracts for the offline screening slice."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ScreenDecision(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class Reference:
    record_id: str
    title: str
    abstract: str = ""
    pmid: str = ""
    doi: str = ""
    year: str = ""
    source: str = ""
    provenance_file: str = ""
    source_hash: str = ""
    raw_index: int = 0
    study_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Reference":
        defaults = {"record_id": "", "title": "", "abstract": "", "pmid": "", "doi": "",
                    "year": "", "source": "", "provenance_file": "", "source_hash": "",
                    "raw_index": 0, "study_type": ""}
        return cls(**{key: raw.get(key, default) for key, default in defaults.items()})


@dataclass(frozen=True)
class DedupDecision:
    record_id: str
    kept_record_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PrismaCounts:
    identified: int
    after_dedup: int
    duplicates_removed: int
    screened: int = 0
    excluded_screening: int = 0
    included: int = 0
    abstained: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ScreenResult:
    record_id: str
    decision: ScreenDecision
    reason: str
    envelope: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["decision"] = self.decision.value
        return value


@dataclass(frozen=True)
class AdjudicationQueue:
    unresolved_record_ids: tuple[str, ...]
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "unresolved_record_ids": list(self.unresolved_record_ids),
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ScreeningPackage:
    package_type: str
    human_adjudication: str
    governed_export_allowed: bool
    results: tuple[ScreenResult, ...]
    adjudication_queue: AdjudicationQueue
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "screening-package-v1",
            "package_type": self.package_type,
            "human_adjudication": self.human_adjudication,
            "governed_export_allowed": self.governed_export_allowed,
            "results": [result.to_dict() for result in self.results],
            "adjudication_queue": self.adjudication_queue.to_dict(),
            "manifest": self.manifest,
        }
