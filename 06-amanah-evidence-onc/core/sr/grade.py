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
_DEFAULT_HIGH_ROB = ["serious", "high", "critical"]


def grade_config() -> dict:
    from core.sr.config import shared_or_vertical
    return yaml.safe_load(shared_or_vertical("grade_config.yaml").read_text(encoding="utf-8")) or {}


def high_rob_labels() -> list[str]:
    """Overall-judgment substrings that count as HIGH risk of bias, from the active
    vertical's rob_config (tool-dependent polarity — NOS inverts RoB2/ROBINS-I)."""
    from core.sr.config import shared_or_vertical
    cfg = yaml.safe_load(shared_or_vertical("rob_config.yaml").read_text(encoding="utf-8")) or {}
    return [str(s).lower() for s in (cfg.get("high_rob_overall") or _DEFAULT_HIGH_ROB)]


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
    rob_by_pmid = {a.pmid: (a.overall or "").lower() for a in (rob or [])}
    high_rob = high_rob_labels()

    rows: list[GradeRow] = []
    for oc in cfg.get("outcomes", []):
        field = oc["field"]
        contributors = [
            es.pmid for es in extractions
            for v in es.values
            if v.field == field and _is_real(v.value, v.flag)
        ]
        contributors = list(dict.fromkeys(contributors))
        n = len(contributors)
        if n == 0:
            rows.append(GradeRow(outcome=oc["name"], field=field, n_studies=0,
                                 certainty="No data", start="—",
                                 deferred=["No included study reported this outcome."]))
            continue
        n_rct = sum(1 for p in contributors if design_by_pmid.get(p, "") in _RCT_DESIGNS)
        start = cfg["start"]["with_rct"] if n_rct > 0 else cfg["start"]["observational_only"]

        downgrades: list[str] = []
        ind = rules.get("indirectness", {})
        if ind.get("always_downgrade"):
            downgrades.append(f"Indirectness (-{ind['always_downgrade']}): {ind.get('reason','')}")
        rb = rules.get("risk_of_bias", {})
        if rob_by_pmid:
            frac = sum(1 for p in contributors
                       if any(h in rob_by_pmid.get(p, "") for h in high_rob)) / n
            if frac > rb.get("serious_fraction_threshold", 0.5):
                downgrades.append(f"Risk of bias (-1): {rb.get('reason','')} ({frac:.0%} serious/high)")
        imp = rules.get("imprecision", {})
        if n < imp.get("min_studies", 3):
            downgrades.append(f"Imprecision (-1): {imp.get('reason','')} (n={n})")

        certainty = _step_down(start, len(downgrades), levels)
        rows.append(GradeRow(
            outcome=oc["name"], field=field, n_studies=n, n_rct=n_rct, start=start,
            downgrades=downgrades, deferred=list(cfg.get("deferred_domains", [])),
            certainty=certainty,
        ))
    return rows
