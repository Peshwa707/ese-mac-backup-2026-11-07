"""Structured, boundary-specified screening criteria (optional per-vertical enrichment).

A vertical MAY supply a ``screening_criteria.yaml`` alongside its ``pico.yaml``. When
present, the screen stage renders these structured criteria — key-term definitions,
in/out boundary conditions, near-miss cases, and an EXPLICIT study-design gate — instead
of the flat inclusion/exclusion prose. When absent, screening falls back to the flat
criteria unchanged (fully backward-compatible).

Motivation (validated on a 3-arm eval, 2026-07): most human-vs-LLM screening divergence
traces to under-specified construct boundaries — especially study-DESIGN eligibility. In a
tiered NMA vertical the flat criteria did not state that real-world/registry evidence is
eligible, so a reviewer false-excluded a Tier-3 cohort. Making design tiers explicit fixed
it (include-recall 0.75 -> 1.0) with no other change. See screening literature: per-criterion
definitions + boundary + near-miss specification reduces semantic drift at screening.

Design eligibility is a FIRST-CLASS criterion: ``all_criteria()`` synthesizes a ``design``
criterion from ``design_eligibility`` (unless the vertical authors an explicit one), so the
design gate is un-skippable and un-ambiguous rather than buried in prose.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_FILENAME = "screening_criteria.yaml"


class Boundaries(BaseModel):
    """In-scope vs out-of-scope exemplars for one criterion (boundary conditions)."""
    # extra='forbid': a misspelled enrichment key (e.g. `boundries`, `near_miss`) must ERROR,
    # not silently drop content — a silent drop is exactly the failure this module prevents.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    in_scope: list[str] = Field(default_factory=list, alias="in")
    out_scope: list[str] = Field(default_factory=list, alias="out")


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["include", "exclude"]
    text: str
    definitions: dict[str, str] = Field(default_factory=dict)
    boundaries: Boundaries = Field(default_factory=Boundaries)
    near_misses: list[str] = Field(default_factory=list)


class DesignEligibility(BaseModel):
    """Explicit study-design eligibility. ``eligible``/``excluded`` are the design types that
    PASS/FAIL the design gate. A tiered NMA lists several (rct, single_arm_trial, real_world_cohort,
    ...); a trial-only review lists just [rct]. ``tier_notes`` is provenance for the downstream
    analysis role and does NOT affect the screen include/exclude decision."""
    model_config = ConfigDict(extra="forbid")
    eligible: list[str] = Field(min_length=1)   # empty -> a design gate nothing can pass
    excluded: list[str]
    rationale: str = ""
    tier_notes: dict[str, str] = Field(default_factory=dict)


class CriteriaSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vertical: str
    design_eligibility: DesignEligibility          # required -> load fails closed if absent
    criteria: list[Criterion]

    @model_validator(mode="after")
    def _unique_ids(self) -> "CriteriaSet":
        ids = [c.id for c in self.criteria]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate criterion id(s): {dupes}")
        return self

    def all_criteria(self) -> list[Criterion]:
        """Explicit criteria PLUS a first-class ``design`` criterion (synthesized from
        ``design_eligibility`` unless the vertical authors its own). Design is placed first."""
        explicit = [c for c in self.criteria if c.id == "design"]
        if explicit:
            return [*explicit, *(c for c in self.criteria if c.id != "design")]
        de = self.design_eligibility
        design = Criterion(
            id="design", kind="include",
            text="The study uses an eligible study design for this review.",
            definitions={
                "eligible designs": ", ".join(de.eligible),
                "NOT eligible": ", ".join(de.excluded),
                **({"rationale": de.rationale} if de.rationale else {}),
            },
            boundaries=Boundaries(in_scope=de.eligible, out_scope=de.excluded),
            near_misses=[
                "A study that merely CITES or is compared against a trial in its background is "
                "not itself that trial — classify by the study's OWN design.",
            ],
        )
        return [design, *self.criteria]


def _render_criterion(c: Criterion) -> str:
    lines = [f"CRITERION [{c.id}] ({c.kind}): {c.text}"]
    if c.definitions:
        lines.append("Definitions:")
        lines += [f"  - {k}: {v}" for k, v in c.definitions.items()]
    if c.boundaries.in_scope:
        lines.append("In scope (examples): " + "; ".join(c.boundaries.in_scope))
    if c.boundaries.out_scope:
        lines.append("Out of scope (examples): " + "; ".join(c.boundaries.out_scope))
    if c.near_misses:
        lines.append("Near-misses / confounders:")
        lines += [f"  - {n}" for n in c.near_misses]
    return "\n".join(lines)


def render_criteria_set(cs: CriteriaSet) -> str:
    """Render a whole CriteriaSet as one prose block for the screening prompt."""
    inc = [c for c in cs.all_criteria() if c.kind == "include"]
    exc = [c for c in cs.all_criteria() if c.kind == "exclude"]
    # Always surface the machine-readable design eligibility, even when the vertical authors its
    # own `design` criterion (which bypasses the synthesized one) — so the eligible/excluded tiers
    # are never silently omitted from the prompt.
    de = cs.design_eligibility
    de_line = (f"STUDY-DESIGN ELIGIBILITY — eligible: {', '.join(de.eligible)}; "
               f"NOT eligible: {', '.join(de.excluded)}."
               + (f" {de.rationale.strip()}" if de.rationale else ""))
    parts = ["ELIGIBILITY CRITERIA (INCLUDE only if EVERY inclusion criterion is satisfied "
             "AND NO exclusion criterion matches):\n", de_line + "\n"]
    if inc:
        parts.append("== INCLUSION (all must be satisfied) ==")
        parts += [_render_criterion(c) + "\n" for c in inc]
    if exc:
        parts.append("== EXCLUSION (any match excludes) ==")
        parts += [_render_criterion(c) + "\n" for c in exc]
    return "\n".join(parts)


@lru_cache(maxsize=None)
def _load_cached(root_str: str) -> CriteriaSet | None:
    root = Path(root_str)
    # NOTE: vertical-LOCAL only — unlike rob/grade/prompts there is no shared_or_vertical fallback;
    # a screening_criteria.yaml dropped in the shared `systematic-review/` dir is NOT inherited.
    path = root / _FILENAME
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping, got {type(data).__name__}")
    data.setdefault("vertical", root.name)
    cs = CriteriaSet.model_validate(data)
    if cs.vertical != root.name:
        raise ValueError(f"{path}: criteria vertical {cs.vertical!r} != directory {root.name!r}")
    if not cs.criteria:
        raise ValueError(f"{path}: at least one criterion required")
    return cs


def load_for_vertical(root: Path) -> CriteriaSet | None:
    """Load + validate a vertical's structured criteria, or return None if it has none.

    ``root`` is the vertical directory (``SRConfig.root``). Fails CLOSED on a malformed file
    (missing design_eligibility, empty criteria, bad shape) so a broken enrichment errors loudly
    rather than silently degrading to weaker screening. Cached per resolved path (parsed once per
    run; mid-run edits are intentionally not re-read, matching SRConfig's own caching)."""
    return _load_cached(str(Path(root)))
