"""Analysis-method registry with applicability preconditions (WP-M4.3, spec §11).

Each statistical method declares what data it is valid for (effect measure, minimum
studies, network connectivity, transitivity). ``select_method`` fails closed when a hard
precondition is violated (e.g. NMA on a disconnected network) and warns on soft issues
(e.g. fixed-effect under high heterogeneity), so an inappropriate method can't be applied
silently — a footgun an academic reviewer would otherwise catch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Method:
    name: str
    effect_measures: tuple[str, ...]          # measures the method is valid for
    min_studies: int = 2
    requires_connected_network: bool = False
    transitivity_required: bool = False
    description: str = ""


REGISTRY: dict[str, Method] = {
    "random_effects": Method(
        "random_effects", ("proportion", "or", "rr", "hr", "md", "smd"), min_studies=2,
        description="DerSimonian-Laird / REML random-effects pooling"),
    "fixed_effect": Method(
        "fixed_effect", ("or", "rr", "hr", "md", "smd"), min_studies=2,
        description="inverse-variance fixed-effect pooling"),
    "frequentist_nma": Method(
        "frequentist_nma", ("or", "rr", "hr", "md"), min_studies=3,
        requires_connected_network=True, transitivity_required=True,
        description="netmeta-style frequentist network meta-analysis"),
    "bayesian_nma": Method(
        "bayesian_nma", ("or", "rr", "hr", "md"), min_studies=3,
        requires_connected_network=True, transitivity_required=True,
        description="gemtc-style Bayesian network meta-analysis"),
}


def select_method(name: str, data_summary: dict) -> tuple[bool, list[str]]:
    """Validate a chosen method against the data. Returns (ok, messages). ``ok`` is False
    when a HARD precondition is violated; messages also include soft warnings."""
    m = REGISTRY.get(name)
    if m is None:
        return False, [f"unknown method {name!r} (known: {sorted(REGISTRY)})"]

    ok = True
    msgs: list[str] = []
    em = data_summary.get("effect_measure")
    k = data_summary.get("k")
    i2 = data_summary.get("i2", data_summary.get("I2"))   # accept both keys (stats emit "I2")

    # effect measure: fail closed if the method declares measures and none/invalid given
    if m.effect_measures and em is None:
        ok = False
        msgs.append(f"{name} requires an effect_measure (valid: {list(m.effect_measures)})")
    elif em is not None and em not in m.effect_measures:
        ok = False
        msgs.append(f"{name} is not applicable to effect measure {em!r} "
                    f"(valid: {list(m.effect_measures)})")

    # study count: fail closed on missing/non-numeric/non-finite/non-integer k
    # (don't TypeError on a bad summary; NaN/inf must not slip past k < min_studies)
    k_valid = (isinstance(k, (int, float)) and not isinstance(k, bool)
               and math.isfinite(k) and k >= 0 and float(k).is_integer())
    if not k_valid:
        ok = False
        msgs.append(f"{name} requires a finite integer study count k (got {k!r})")
    elif k < m.min_studies:
        ok = False
        msgs.append(f"{name} requires >= {m.min_studies} studies (k={k})")

    if m.requires_connected_network and not data_summary.get("network_connected", False):
        ok = False
        msgs.append(f"{name} requires a CONNECTED comparator network")

    # soft warnings (do not fail) ------------------------------------------------
    if m.transitivity_required and not data_summary.get("transitivity_assessed", False):
        msgs.append(f"{name}: transitivity & consistency must be assessed before pooling")
    if name == "fixed_effect" and isinstance(i2, (int, float)) and not isinstance(i2, bool) and i2 > 50:
        msgs.append(f"fixed-effect under high heterogeneity (I2={i2}%) — prefer random-effects")
    if name == "random_effects" and k_valid and k < 3:
        msgs.append("random-effects tau^2 is unstable with very few studies (k<3)")
    return ok, msgs
