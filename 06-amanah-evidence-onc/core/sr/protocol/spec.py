"""ProtocolSpec — the validated intermediate between a rough draft and the engine config.

An agent fills this (interactively or via llm.parse); compile.py renders the protocol doc +
vertical YAML from it. Pydantic validators enforce internal consistency BEFORE anything is
written, so a compiled vertical is structurally sound by construction.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

_FRAMINGS = {"pico", "pcc"}
_PROFILES = {"systematic", "scoping", "rapid", "living"}
_ROB_TOOLS = {"auto", "rob2", "robins_i", "nos"}
_LEVELS = {"abstract", "fulltext"}
# Designs that route to Newcastle-Ottawa (observational) rather than RoB 2 / ROBINS-I.
_OBSERVATIONAL = {"cohort", "case-control", "nested case-control", "case-cohort",
                  "cross-sectional", "prospective", "retrospective", "observational"}


class ConceptBlock(BaseModel):
    """One AND-group of the search: MeSH headings OR free-text [tiab] terms."""
    name: str                                   # e.g. "population", "exposure", "design"
    terms: list[str] = Field(default_factory=list)   # free-text (auto-tagged [tiab])
    mesh: list[str] = Field(default_factory=list)    # MeSH headings (tagged [Mesh])

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.terms and not self.mesh:
            raise ValueError(f"concept block '{self.name}' has no terms or MeSH")
        return self


class SearchSpec(BaseModel):
    concept_blocks: list[ConceptBlock]
    databases: list[str] = Field(default_factory=lambda: ["pubmed"])
    date_from: str = ""        # "YYYY/MM/DD" or ""
    date_to: str = ""
    retmax: int = 3000

    @field_validator("concept_blocks")
    @classmethod
    def _at_least_one(cls, v):
        if not v:
            raise ValueError("search needs >= 1 concept block")
        return v


class ExtractionField(BaseModel):
    name: str
    level: str = "abstract"

    @field_validator("level")
    @classmethod
    def _level(cls, v):
        return v if v in _LEVELS else "abstract"


class ExtractionGroup(BaseModel):
    group: str                                  # population | efficacy | safety | dose_response ...
    fields: list[ExtractionField]


class Outcome(BaseModel):
    name: str
    pool_field: str = ""        # extraction field this outcome pools on ("" = narrative only)


class AnalysisPlan(BaseModel):
    effect_measure: str = "RR/OR/HR (generic inverse-variance, REML)"
    dose_response: bool = False
    subgroups: list[str] = Field(default_factory=list)
    publication_bias: bool = True


class ProtocolSpec(BaseModel):
    """Everything needed to render a review protocol AND a runnable engine vertical."""
    title: str
    short_id: str
    framing: str = "pico"
    profile: str = "systematic"
    background: str = ""                         # 1-3 sentences for the protocol doc

    # PECO/PICO (population/intervention=exposure/comparator)
    population: str
    intervention: str                            # the exposure/intervention
    comparator: str = ""
    outcomes_primary: list[Outcome] = Field(default_factory=list)
    outcomes_secondary: list[Outcome] = Field(default_factory=list)

    designs: list[str] = Field(default_factory=list)
    inclusion: list[str]
    exclusion: list[str]

    search: SearchSpec
    rob_tool: str = "auto"
    analysis: AnalysisPlan = Field(default_factory=AnalysisPlan)
    extraction_groups: list[ExtractionGroup]

    registration: str = "PROSPERO: [DRAFT — not yet submitted]"
    output_base: str = ""
    disclaimer: str = ""

    @field_validator("framing")
    @classmethod
    def _framing(cls, v):
        if v not in _FRAMINGS:
            raise ValueError(f"framing must be one of {_FRAMINGS}")
        return v

    @field_validator("profile")
    @classmethod
    def _profile(cls, v):
        if v not in _PROFILES:
            raise ValueError(f"profile must be one of {_PROFILES}")
        return v

    @field_validator("rob_tool")
    @classmethod
    def _rob(cls, v):
        if v not in _ROB_TOOLS:
            raise ValueError(f"rob_tool must be one of {_ROB_TOOLS}")
        return v

    @field_validator("inclusion", "exclusion")
    @classmethod
    def _crit_nonempty(cls, v):
        if not v:
            raise ValueError("eligibility criteria must be non-empty")
        return v

    @model_validator(mode="after")
    def _outcomes_reference_real_fields(self):
        field_names = {f.name for g in self.extraction_groups for f in g.fields}
        for oc in (self.outcomes_primary + self.outcomes_secondary):
            if oc.pool_field and oc.pool_field not in field_names:
                raise ValueError(
                    f"outcome '{oc.name}' pools on '{oc.pool_field}', which is not an "
                    f"extraction field {sorted(field_names)}")
        return self

    def is_observational(self) -> bool:
        if self.rob_tool == "nos":
            return True
        if self.rob_tool in ("rob2", "robins_i"):
            return False
        return any(d.strip().lower() in _OBSERVATIONAL for d in self.designs)

    def field_names(self) -> set[str]:
        return {f.name for g in self.extraction_groups for f in g.fields}
