"""Protocol-supplied synthesis calculations: single-study effect measures,
uncertainty flags, and byte-stable calculation artifacts.

This module calculates exactly one thing at a time: a single effect measure
for a single study's protocol-supplied 2x2 count table, using the fixed,
documented formula named by an `AnalysisRecipe`. It never pools or combines
quantities across more than one study or record -- cross-study meta-analytic
pooling is out of scope for this offline slice.

Every calculation is bounded to one of `SUPPORTED_EFFECT_MEASURES`
(`risk_ratio`, `risk_difference`, `odds_ratio`), computed with their
standard, citable formulas and a fixed two-sided 95% normal-approximation
confidence interval. When a cell count makes the standard log-scale formula
undefined for a ratio measure (zero events in an arm, or -- for the odds
ratio -- zero non-events in an arm), the calculation is never silently
"fixed" with an invented continuity correction: it is recorded with status
`insufficient_data` and an explicit `uncertainty_flags` entry naming the
reason, and no point estimate or interval is computed.

Every calculation artifact is bound to a `calculation_hash` over its exact
content, mirroring the `object_hash`/`IntegrityError` tamper-detection
pattern used throughout `contracts.py`, `dedup.py`, `evaluation.py`,
`replay.py`, `extraction.py`, and `risk_of_bias.py`. `verify_calculation`
fully recomputes the calculation from its own recorded `record_id`,
`measure`, and `inputs`, so a tampered `point_estimate`, `ci_low`/`ci_high`,
`status`, or `uncertainty_flags` is caught, not just a tampered hash.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .contracts import IntegrityError, object_hash

ANALYSIS_RECIPE_VERSION = "analysis-recipe-v1"
CALCULATION_VERSION = "synthesis-calculation-v1"
SUPPORTED_EFFECT_MEASURES = ("risk_ratio", "risk_difference", "odds_ratio")

# Two-sided 95% normal-approximation critical value; this is the only
# confidence level currently implemented.
Z_95 = 1.959963984540054

_COUNT_FIELDS = ("treated_events", "treated_total", "control_events", "control_total")


def build_analysis_recipe(record_id: str, measure: str) -> dict[str, Any]:
    """Build a protocol-supplied recipe naming exactly one bounded calculation.

    `measure` must be one of `SUPPORTED_EFFECT_MEASURES`; an unlisted or
    invented measure is rejected, never coerced to the nearest known one.
    """
    if not isinstance(record_id, str) or not record_id.strip():
        raise IntegrityError("analysis recipe requires a non-empty record_id")
    if measure not in SUPPORTED_EFFECT_MEASURES:
        raise IntegrityError(f"unsupported effect measure: {measure!r}")
    return {
        "version": ANALYSIS_RECIPE_VERSION,
        "record_id": record_id.strip(),
        "measure": measure,
    }


def _validate_counts(counts: Mapping[str, Any]) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in _COUNT_FIELDS:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise IntegrityError(f"{key}: count must be an integer")
        if value < 0:
            raise IntegrityError(f"{key}: count must not be negative")
        values[key] = value
    if values["treated_events"] > values["treated_total"]:
        raise IntegrityError("treated_events must not exceed treated_total")
    if values["control_events"] > values["control_total"]:
        raise IntegrityError("control_events must not exceed control_total")
    if values["treated_total"] == 0 or values["control_total"] == 0:
        raise IntegrityError("treated_total and control_total must both be greater than zero")
    return values


def _zero_cell_flags(measure: str, a: int, n1: int, b: int, n0: int) -> list[str]:
    flags: list[str] = []
    if measure in ("risk_ratio", "odds_ratio") and (a == 0 or b == 0):
        flags.append("zero_event_cell_ratio_measure_undefined")
    if measure == "odds_ratio" and (a == n1 or b == n0):
        flags.append("zero_nonevent_cell_odds_ratio_undefined")
    return flags


def calculate_effect_measure(recipe: Mapping[str, Any], counts: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate exactly the measure `recipe` names from a protocol-supplied
    2x2 `counts` table (`treated_events`, `treated_total`, `control_events`,
    `control_total`).

    Raises `IntegrityError` if `recipe` is not a supported version/measure,
    or if `counts` fails validation (non-integer, negative, events exceeding
    total, or a zero total). If the requested measure's standard formula is
    undefined for the supplied counts, returns a `status: "insufficient_data"`
    artifact carrying the reason in `uncertainty_flags` instead of computing
    (or guessing) a value.
    """
    if recipe.get("version") != ANALYSIS_RECIPE_VERSION:
        raise IntegrityError(f"unsupported analysis recipe version: {recipe.get('version')}")
    measure = recipe.get("measure")
    if measure not in SUPPORTED_EFFECT_MEASURES:
        raise IntegrityError(f"unsupported effect measure: {measure!r}")
    record_id = recipe.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise IntegrityError("analysis recipe requires a non-empty record_id")

    values = _validate_counts(counts)
    a, n1 = values["treated_events"], values["treated_total"]
    b, n0 = values["control_events"], values["control_total"]
    inputs = dict(values)

    flags = _zero_cell_flags(measure, a, n1, b, n0)
    if flags:
        body = {
            "version": CALCULATION_VERSION,
            "record_id": record_id.strip(),
            "measure": measure,
            "status": "insufficient_data",
            "inputs": inputs,
            "point_estimate": None,
            "ci_low": None,
            "ci_high": None,
            "uncertainty_flags": flags,
        }
        return {**body, "calculation_hash": object_hash(body)}

    p1 = a / n1
    p0 = b / n0
    if measure == "risk_ratio":
        point = p1 / p0
        se = math.sqrt(1 / a - 1 / n1 + 1 / b - 1 / n0)
        ci_low = math.exp(math.log(point) - Z_95 * se)
        ci_high = math.exp(math.log(point) + Z_95 * se)
    elif measure == "odds_ratio":
        point = (a * (n0 - b)) / (b * (n1 - a))
        se = math.sqrt(1 / a + 1 / (n1 - a) + 1 / b + 1 / (n0 - b))
        ci_low = math.exp(math.log(point) - Z_95 * se)
        ci_high = math.exp(math.log(point) + Z_95 * se)
    else:  # risk_difference
        point = p1 - p0
        se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        ci_low = point - Z_95 * se
        ci_high = point + Z_95 * se

    body = {
        "version": CALCULATION_VERSION,
        "record_id": record_id.strip(),
        "measure": measure,
        "status": "calculated",
        "inputs": inputs,
        "point_estimate": point,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "uncertainty_flags": [],
    }
    return {**body, "calculation_hash": object_hash(body)}


def verify_calculation(calculation: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a stored calculation artifact by fully recomputing it.

    Raises `IntegrityError` if `calculation_hash` does not match the
    artifact's content (tamper detection), if the artifact version is
    unsupported, if `inputs` is missing or not a mapping, or if a fresh
    recomputation from the artifact's own recorded `record_id`, `measure`,
    and `inputs` does not exactly match the stored `status`,
    `point_estimate`, `ci_low`, `ci_high`, and `uncertainty_flags` -- the
    same construction-time logic in `calculate_effect_measure` is re-run
    here, not just recomputed as a hash.
    """
    body = {key: value for key, value in calculation.items() if key != "calculation_hash"}
    if calculation.get("calculation_hash") != object_hash(body):
        raise IntegrityError("calculation_hash does not match calculation content")
    if body.get("version") != CALCULATION_VERSION:
        raise IntegrityError(f"unsupported synthesis calculation version: {body.get('version')}")
    record_id = body.get("record_id")
    measure = body.get("measure")
    inputs = body.get("inputs")
    if not isinstance(inputs, Mapping):
        raise IntegrityError("synthesis calculation requires an 'inputs' mapping")
    recipe = build_analysis_recipe(
        record_id if isinstance(record_id, str) else "",
        measure if isinstance(measure, str) else "",
    )
    recomputed = calculate_effect_measure(recipe, inputs)
    recomputed_body = {key: value for key, value in recomputed.items() if key != "calculation_hash"}
    if recomputed_body != body:
        raise IntegrityError(
            "synthesis calculation does not match a fresh recomputation from its recorded inputs"
        )
    return body
