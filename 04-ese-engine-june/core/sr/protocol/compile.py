"""Render a ProtocolSpec into BOTH the human protocol.md and the engine vertical YAML.

Deterministic: the LLM produced the validated ProtocolSpec; this module assembles the files,
so the engine config (which SRConfig reads verbatim) is never hand-typed by the model.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.sr.protocol.pubmed_query import build_query
from core.sr.protocol.spec import ProtocolSpec

_EXTRACTION_RULES = [
    "Copy values verbatim from the source text only. Do not use outside knowledge.",
    "If a field is not stated, set value to 'NR'. Derived/computed values: prefix 'ESTIMATED:'.",
    "For each non-NR numeric value, include the exact verbatim supporting quote.",
    "Effect estimates must be the categorical highest-vs-lowest contrast WITH its 95% CI; "
    "a continuous per-increment estimate is NOT a substitute — set NR if only that exists.",
]


def _dump(obj: dict) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)


# --- vertical YAML -------------------------------------------------------------

def _pico_yaml(spec: ProtocolSpec) -> dict:
    review = {"title": spec.title, "short_id": spec.short_id,
              "registration": spec.registration, "output_base": spec.output_base}
    doc: dict = {"review": review}
    if spec.framing == "pcc":
        doc["pcc"] = {"population": spec.population, "concept": spec.intervention,
                      "context": spec.comparator}
    else:
        doc["pico"] = {
            "population": spec.population,
            "intervention": spec.intervention,
            "comparator": spec.comparator,
            "outcomes": {"primary": [o.name for o in spec.outcomes_primary],
                         "secondary": [o.name for o in spec.outcomes_secondary]},
        }
    doc["eligibility"] = {"inclusion": spec.inclusion, "exclusion": spec.exclusion}
    doc["disclaimer"] = spec.disclaimer or (
        "AI-drafted. Every PMID live-verified; values abstract/full-text-anchored or marked NR; "
        "nothing fabricated. Independent human verification and sign-off "
        "(reviewer-of-record) required before any output is treated as final.")
    return doc


def _search_yaml(spec: ProtocolSpec) -> dict:
    q = build_query(spec.search.concept_blocks, spec.search.date_from, spec.search.date_to)
    doc: dict = {
        "disclaimer": "Auto-generated from the protocol concept blocks; review the PubMed string "
                      "and translate to each non-PubMed database before importing.",
        "pubmed": {"executed": True, "query": q, "retmax": spec.search.retmax},
    }
    others = [db for db in spec.search.databases if db.lower() != "pubmed"]
    if others:
        doc["other_databases"] = [
            {"name": db, "executed": False,
             "note": "translate concept blocks to this database; import exported records "
                     "(provenance-attested)."} for db in others]
    doc["named_trials"] = []
    doc["reference_mining_seeds"] = []
    return doc


def _extraction_yaml(spec: ProtocolSpec) -> dict:
    pooled = {
        oc.pool_field: oc.name
        for oc in (spec.outcomes_primary + spec.outcomes_secondary)
        if oc.pool_field
    }
    fields = {}
    for group in spec.extraction_groups:
        rows = []
        for field in group.fields:
            row = {"name": field.name, "level": field.level}
            if field.name in pooled:
                row["pool"] = "log_effect"
                row["label"] = pooled[field.name]
            rows.append(row)
        fields[group.group] = rows
    return {"fields": fields, "rules": list(_EXTRACTION_RULES)}


def _nos_rob_yaml() -> dict:
    """Newcastle-Ottawa config for observational designs (cohort + case-control)."""
    return {
        "tool_by_design": {
            "cohort": "nos_cohort", "case-cohort": "nos_cohort", "prospective": "nos_cohort",
            "registry": "nos_cohort", "other": "nos_cohort",
            "case-control": "nos_cc", "nested case-control": "nos_cc",
        },
        "judgments": {
            "nos_cohort": ["*", "**", "--", "No information"],
            "nos_cc": ["*", "**", "--", "No information"],
        },
        "tools": {
            "nos_cohort": {
                "name": "Newcastle-Ottawa Scale — cohort studies (max 9 stars; high quality >=7)",
                "domains": [
                    {"key": "S1", "label": "Selection: representativeness of the exposed cohort"},
                    {"key": "S2", "label": "Selection: selection of the non-exposed cohort (same source)"},
                    {"key": "S3", "label": "Selection: ascertainment of exposure (validated instrument)"},
                    {"key": "S4", "label": "Selection: outcome not present at start of study"},
                    {"key": "C1", "label": "Comparability: adjusts for the key confounders (up to **)"},
                    {"key": "O1", "label": "Outcome: assessment of outcome (registry / linkage)"},
                    {"key": "O2", "label": "Outcome: follow-up long enough for the outcome to occur"},
                    {"key": "O3", "label": "Outcome: adequacy / completeness of follow-up"},
                ],
            },
            "nos_cc": {
                "name": "Newcastle-Ottawa Scale — case-control studies (max 9 stars; high quality >=7)",
                "domains": [
                    {"key": "S1", "label": "Selection: case definition adequate (confirmed)"},
                    {"key": "S2", "label": "Selection: representativeness of the cases"},
                    {"key": "S3", "label": "Selection: selection of controls (same base population)"},
                    {"key": "S4", "label": "Selection: definition of controls (no outcome history)"},
                    {"key": "C1", "label": "Comparability: adjusts for the key confounders (up to **)"},
                    {"key": "E1", "label": "Exposure: ascertainment of exposure (validated/blinded)"},
                    {"key": "E2", "label": "Exposure: same ascertainment method for cases and controls"},
                    {"key": "E3", "label": "Exposure: non-response rate same for both groups"},
                ],
            },
        },
    }


def _observational_grade_yaml(spec: ProtocolSpec) -> dict:
    outcomes = [{"name": o.name, "field": o.pool_field}
                for o in (spec.outcomes_primary + spec.outcomes_secondary) if o.pool_field]
    return {
        "levels": ["High", "Moderate", "Low", "Very Low"],
        "start": {"with_rct": "High", "observational_only": "Low"},
        "rules": {
            "indirectness": {"always_downgrade": 0, "reason": ""},
            "risk_of_bias": {"serious_fraction_threshold": 0.5,
                             "reason": "Most contributing studies at high risk of bias (NOS)."},
            "imprecision": {"min_studies": 3, "reason": "Few contributing studies / sparse data."},
        },
        "deferred_domains": [
            "Inconsistency (I^2) — confirm during independent human verification once pooled.",
            "Publication bias — not assessable below k>=10 (Egger/Begg/trim-and-fill underpowered).",
            "Possible UPGRADE for large effect / exposure-response gradient — deferred to independent human verification.",
        ],
        "outcomes": outcomes or [{"name": "Primary outcome", "field": ""}],
    }


def compile_vertical(spec: ProtocolSpec, dest: Path) -> list[Path]:
    """Write the runnable vertical (3 required + 2 optional YAML files). Returns written paths."""
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _w(name: str, obj: dict) -> None:
        p = dest / name
        p.write_text(_dump(obj), encoding="utf-8")
        written.append(p)

    _w("pico.yaml", _pico_yaml(spec))
    _w("search_strategies.yaml", _search_yaml(spec))
    _w("extraction_schema.yaml", _extraction_yaml(spec))
    # RoB/GRADE overrides only when non-standard (observational -> NOS); RCT uses shared defaults.
    if spec.is_observational():
        _w("rob_config.yaml", _nos_rob_yaml())
        _w("grade_config.yaml", _observational_grade_yaml(spec))
    return written


# --- human protocol document ---------------------------------------------------

def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- [NEEDS HUMAN INPUT]"


def render_protocol_md(spec: ProtocolSpec) -> str:
    framing = "PCC" if spec.framing == "pcc" else "PICO"
    q = build_query(spec.search.concept_blocks, spec.search.date_from, spec.search.date_to)
    prim = "\n".join(f"- {o.name}" + (f"  _(pools on `{o.pool_field}`)_" if o.pool_field else
                                      "  _[narrative only — no registered poolable field]_")
                     for o in spec.outcomes_primary) or "- [NEEDS HUMAN INPUT]"
    sec = "\n".join(f"- {o.name}" for o in spec.outcomes_secondary) or "- (none)"
    rob = ("Newcastle-Ottawa Scale (NOS; >=7/9 high quality)" if spec.is_observational()
           else "Cochrane RoB 2.0 (RCTs) / ROBINS-I (non-randomized) — auto-selected by design")
    field_rows = "\n".join(
        f"| {g.group} | {', '.join(f.name + ('*' if f.level == 'fulltext' else '') for f in g.fields)} |"
        for g in spec.extraction_groups)
    subgroups = ", ".join(spec.analysis.subgroups) if spec.analysis.subgroups else "(none pre-specified)"
    peco = (f"| **Population** | {spec.population} |\n"
            f"| **{'Concept' if spec.framing=='pcc' else 'Exposure/Intervention'}** | {spec.intervention} |\n"
            f"| **{'Context' if spec.framing=='pcc' else 'Comparator'}** | {spec.comparator or '[NEEDS HUMAN INPUT]'} |\n"
            f"| **Designs** | {', '.join(spec.designs) or '[NEEDS HUMAN INPUT]'} |")
    return f"""# Protocol — {spec.title}

> AI-drafted protocol ({spec.profile} review, {framing} framing). Every assumption the rough
> draft did not specify is marked **[NEEDS HUMAN INPUT]**. No evidence or results are asserted.
> Registration: {spec.registration}

## 1. Background & objective
{spec.background or '[NEEDS HUMAN INPUT] — burden, rationale, and the a-priori hypothesis.'}

## 2. Review question ({framing})
| Element | Definition |
|---|---|
{peco}

## 3. Eligibility criteria
**Include (all must hold):**
{_bullets(spec.inclusion)}

**Exclude (any excludes):**
{_bullets(spec.exclusion)}

## 4. Information sources & search strategy
Databases: {', '.join(spec.search.databases)}. Date range: {spec.search.date_from or 'inception'} – {spec.search.date_to or 'search execution'}.

PubMed search string (auto-generated; verify before running):
```
{q}
```

## 5. Study selection
Title/abstract → full-text against §3; `include`/`exclude`/`uncertain` with a one-line reason per record. AI reviewer model(s) draft the decisions; an independent human verification pass (reviewer-of-record) is required.

## 6. Data extraction dictionary
Fields marked * are full-text-level (else abstract-level):

| Group | Fields |
|---|---|
{field_rows}

## 7. Risk of bias
{rob}.

## 8. Synthesis & statistical analysis
- **Primary:** highest-vs-lowest pooling — {spec.analysis.effect_measure}.
- **Outcomes (primary):**
{prim}
- **Secondary outcomes:**
{sec}
- **Heterogeneity:** Cochran Q + I².
- **Subgroups:** {subgroups}.
- **Sensitivity:** leave-one-out.
- **Dose-response:** {'requested (dosresmeta + RCS; runs only with real per-category data, else reported not-performed)' if spec.analysis.dose_response else 'not requested'}.
- **Publication bias:** {'Egger/Begg/trim-and-fill (performed only at k>=10)' if spec.analysis.publication_bias else 'not requested'}.

## 9. Registration & honesty
{spec.registration}. AI-drafted; cite-or-omit; quote-anchored values; independent human verification and sign-off (reviewer-of-record) required before any output is final.
"""
