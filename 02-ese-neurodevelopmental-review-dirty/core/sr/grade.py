"""Deterministic GRADE certainty-of-evidence (protocol Step 9).

Certainty is computed by rule from the contributing studies' designs and
risk-of-bias profile — no LLM, fully reproducible. Domains that genuinely need
pooled statistics (inconsistency I², publication bias) are reported as deferred to
Phase B rather than guessed. Every row is a draft for Reviewer-2 confirmation.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from core.sr.models import GradeRow, RoBAssessment, ScreeningResult

_RCT_DESIGNS = {"rct", "randomized", "randomised"}


def _is_rct(design: str) -> bool:
    """RCT detection tolerant of the LLM's free-text design string ("randomized
    controlled trial", "RCT", "randomised trial") — but NOT "non-randomized". Exact-set
    matching would drop a genuine RCT body to observational/Low starting certainty."""
    d = (design or "").lower()
    # Exclude quasi-/non-/un-randomized (selection-bias-prone, NOT a High-start RCT body)
    # before the positive match, since "randomi" is a substring of "quasi-randomized".
    if any(x in d for x in ("quasi", "non-random", "nonrandom", "un-random", "unrandom",
                            "not random", "pseudo-random", "pseudorandom")):
        return False
    return "rct" in d or "randomi" in d
_DEFAULT_HIGH_ROB = ["serious", "high", "critical"]


def grade_config() -> dict:
    from core.sr.config import shared_or_vertical
    return yaml.safe_load(shared_or_vertical("grade_config.yaml").read_text(encoding="utf-8")) or {}


def high_rob_labels(tool: str | None = None) -> list[str] | dict[str, list[str]]:
    """Return exact normalized high-risk judgments, optionally per tool.

    AMSTAR-2 uses the opposite polarity from RoB 2/ROBINS-I (``High`` is best),
    so a single global substring list is unsafe.  New configs declare a mapping;
    legacy list configs remain supported for older verticals.
    """
    from core.sr.config import shared_or_vertical
    cfg = yaml.safe_load(shared_or_vertical("rob_config.yaml").read_text(encoding="utf-8")) or {}
    raw = cfg.get("high_rob_overall") or _DEFAULT_HIGH_ROB
    if isinstance(raw, dict):
        normalized = {
            str(k).lower(): [" ".join(str(s).lower().split()) for s in (v or [])]
            for k, v in raw.items()
        }
        return normalized.get((tool or "").lower(), []) if tool else normalized
    return [" ".join(str(s).lower().split()) for s in raw]


def _high_risk(assessment: RoBAssessment, labels) -> bool:
    judgment = " ".join((assessment.overall or "").lower().split())
    if isinstance(labels, dict):
        allowed = labels.get((assessment.tool or "").lower(), [])
    else:
        allowed = labels
    return judgment in allowed


def _metadata_values(es, screening_by_pmid: dict[str, ScreeningResult], field: str) -> list[str]:
    """Return one or more controlled stratum values for a study contribution."""
    if field == "criterion_ids":
        values = [str(v) for v in (getattr(es, "criterion_ids", []) or []) if str(v).strip()]
        return values or ["not_reported"]
    raw = str(getattr(es, field, "") or "")
    if not raw and field == "evidence_stream":
        raw = str(getattr(screening_by_pmid.get(es.pmid), "evidence_stream", "") or "")
    if not raw and field == "diagnosis":
        raw = str(getattr(screening_by_pmid.get(es.pmid), "diagnosis", "") or "")
    if not raw and field == "age_stratum":
        raw = "not_reported"
    return [raw or "not_reported"]


def _is_real(value: str, flag: str) -> bool:
    return flag != "NR" and (value or "").strip().upper() != "NR"


def _step_down(start: str, n: int, levels: list[str]) -> str:
    try:
        i = levels.index(start)
    except ValueError:
        i = levels.index("Low")
    return levels[min(i + n, len(levels) - 1)]


def grade_outcomes(
    extractions: list,
    screening: list[ScreeningResult],
    rob: list[RoBAssessment] | None = None,
) -> list[GradeRow]:
    cfg = grade_config()
    levels = cfg.get("levels", ["High", "Moderate", "Low", "Very Low"])
    rules = cfg.get("rules", {})
    design_by_pmid = {s.pmid: (s.study_design or "").lower() for s in screening}
    rob_by_pmid = {a.pmid: a for a in (rob or [])}
    high_rob = high_rob_labels()
    screening_by_pmid = {s.pmid: s for s in screening}
    stratify_by = [str(x) for x in (cfg.get("stratify_by") or [])]

    rows: list[GradeRow] = []
    for oc in cfg.get("outcomes", []):
        field = oc["field"]
        by_pmid = {}
        for es in extractions:
            if any(v.field == field and _is_real(v.value, v.flag) for v in es.values):
                keys = ["overall"]
                for sf in stratify_by:
                    values = _metadata_values(es, screening_by_pmid, sf)
                    keys = [f"{key}|{sf}={value}" for key in keys for value in values]
                for key in keys:
                    by_pmid.setdefault(key, []).append(es.pmid)
        if not by_pmid:
            by_pmid = {"overall": []}

        for stratum, raw_contributors in by_pmid.items():
            contributors = list(dict.fromkeys(raw_contributors))
            n = len(contributors)
            if n == 0:
                rows.append(GradeRow(outcome=oc["name"], field=field, stratum=stratum,
                                     n_studies=0, certainty="No data", start="—",
                                     deferred=["No included study reported this outcome in this stratum."]))
                continue
            n_rct = sum(1 for p in contributors if _is_rct(design_by_pmid.get(p, "")))
            start = cfg["start"]["with_rct"] if n_rct > 0 else cfg["start"]["observational_only"]

            downgrades: list[str] = []
            ind = rules.get("indirectness", {})
            if ind.get("always_downgrade"):
                downgrades.append(f"Indirectness (-{ind['always_downgrade']}): {ind.get('reason','')}")
            rb = rules.get("risk_of_bias", {})
            rob_assessments = {p: rob_by_pmid.get(p) for p in contributors}
            missing_rob = [p for p, assessment in rob_assessments.items() if assessment is None]
            force_very_low = False
            if rb.get("require_complete") and missing_rob:
                force_very_low = True
                downgrades.append(
                    f"Risk of bias: missing assessment for {len(missing_rob)}/{n} contributors; "
                    "certainty forced to Very Low pending human appraisal."
                )
            elif rob_by_pmid:
                high_count = sum(
                    1 for p in contributors
                    if rob_assessments[p] is not None and _high_risk(rob_assessments[p], high_rob)
                )
                frac = high_count / n
                if frac > rb.get("serious_fraction_threshold", 0.5):
                    downgrades.append(f"Risk of bias (-1): {rb.get('reason','')} ({frac:.0%} high risk)")
            if rules.get("require_full_text"):
                by_pmid = {es.pmid: es for es in extractions}
                abstract_only = [
                    p for p in contributors
                    if not bool(getattr(by_pmid[p], "full_text_verified", False))
                    or str(getattr(by_pmid[p], "evidence_level", "abstract_only")) != "full_text"
                ]
                if abstract_only:
                    force_very_low = True
                    downgrades.append(
                        f"Evidence level: {len(abstract_only)}/{n} contributors lack verified full text; "
                        "certainty forced to Very Low pending full-text extraction."
                    )
            imp = rules.get("imprecision", {})
            if n < imp.get("min_studies", 3):
                downgrades.append(f"Imprecision (-1): {imp.get('reason','')} (n={n})")

            certainty = "Very Low" if force_very_low else _step_down(start, len(downgrades), levels)
            rows.append(GradeRow(
                outcome=oc["name"], field=field, stratum=stratum, n_studies=n, n_rct=n_rct, start=start,
                downgrades=downgrades, deferred=list(cfg.get("deferred_domains", [])),
                certainty=certainty,
            ))
    return rows
