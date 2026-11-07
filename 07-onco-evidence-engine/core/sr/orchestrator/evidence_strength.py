"""Evidence-strength gate (WP-M4.1, spec §7b) — do-no-harm.

Inspects the pooled stats and flags outcomes whose headline estimate would mislead:
  * pooled PROPORTIONS are single-arm -> always NON-COMPARATIVE (firewalled from
    comparative claims);
  * any outcome with excess heterogeneity (I² > threshold) or too few studies
    (k < floor) -> suppress the headline point estimate and force a prediction interval.

Operates on the stats.json the stats adapter writes (PoolResult/HRPoolResult shapes).
Deterministic; its report feeds the manuscript export honesty ledger.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from core.sr.orchestrator.adapters import _hash_of, read_json, write_json
from core.sr.orchestrator.protocol_critic import (_DEF_PHRASES, _DEF_TOKENS, _matches,
                                                  _SUR_PHRASES, _SUR_TOKENS)
from core.sr.orchestrator.stages import RunContext, StageResult


# Any "<word>-free survival" / "<word>-free" interval is a surrogate (PFS, DFS, EFS, RFS,
# FFS, ...). Definitive (overall survival) is checked first so it is never caught here.
_FREE_SURVIVAL = re.compile(r"\b[a-z]+-free\b", re.I)


def classify_endpoint(name: str) -> str:
    """definitive (OS/QoL/mortality) | surrogate (ORR/PFS/MRD/any *-free survival) | unknown.
    Reuses the protocol-critic token/phrase matcher (whole-word for short abbreviations)."""
    lc = (name or "").lower()
    if _matches(lc, _DEF_TOKENS, _DEF_PHRASES):
        return "definitive"
    if _matches(lc, _SUR_TOKENS, _SUR_PHRASES) or _FREE_SURVIVAL.search(lc):
        return "surrogate"
    return "unknown"


_SUPERIORITY = re.compile(r"\b(superior|better than|more effective|outperform\w*)\b", re.I)
_CLINICAL_BENEFIT = re.compile(
    r"(improv\w+\s+(overall\s+)?survival|prolong\w+\s+survival|survival benefit|"
    r"clinical benefit|cure[sd]?)", re.I)
_NEGATION = re.compile(r"\b(no|not|never|without|fail\w*|absent|lack\w*|did\s*n.?t|"
                       r"does\s*n.?t|cannot)\b", re.I)


def _claims(pattern: re.Pattern, text: str) -> bool:
    """True if ``pattern`` appears as an ASSERTION (not immediately negated) in text."""
    for m in pattern.finditer(text or ""):
        if not _NEGATION.search(text[max(0, m.start() - 22):m.start()]):
            return True
    return False


def lint_conclusion(text: str, flags: list[dict]) -> list[dict]:
    """Block over-claims: superiority language without an adequately-certain DIRECT
    comparative estimate, and clinical-benefit/survival claims not backed by a definitive
    endpoint. Negated phrasings ("no survival benefit") are not flagged."""
    has_comparative = any(f["kind"] == "hazard_ratio" and not f["suppress_headline"]
                          and not f.get("grade_indirectness_downgrade") for f in flags)
    # a definitive endpoint only supports a clinical-benefit claim if it is also DIRECT
    # (an indirect/NMA OS estimate is not strong enough to assert clinical benefit)
    has_definitive = any(classify_endpoint(f["outcome"]) == "definitive"
                         and not f["suppress_headline"]
                         and not f.get("grade_indirectness_downgrade") for f in flags)
    out: list[dict] = []
    if _claims(_SUPERIORITY, text) and not has_comparative:
        out.append({"rule": "unsupported_superiority",
                    "message": "Superiority/comparative language without an adequately-certain "
                               "DIRECT comparative estimate."})
    if _claims(_CLINICAL_BENEFIT, text) and not has_definitive:
        out.append({"rule": "surrogate_as_benefit",
                    "message": "Clinical-benefit/survival claim not supported by a definitive "
                               "(OS/QoL) endpoint."})
    return out

I2_MAX = 75.0          # spec §7b default heterogeneity ceiling
K_FLOOR_PROP = 5       # min studies to headline a pooled proportion
K_FLOOR_HR = 3         # min studies to headline a pooled hazard ratio


def _flag(outcome: str, r: dict, *, kind: str, non_comparative: bool,
          i2_max: float, k_floor: int, indirect: bool) -> dict:
    k = r.get("k") or 0
    i2 = r.get("I2")
    reasons: list[str] = []
    if not isinstance(i2, (int, float)) or isinstance(i2, bool) or not math.isfinite(i2):
        # missing / non-numeric / NaN / inf -> heterogeneity cannot be verified; fail closed
        # (don't headline an unassessed pool)
        reasons.append("I² unavailable (heterogeneity could not be verified)")
    elif i2 > i2_max:
        reasons.append(f"I2={i2}% exceeds {i2_max}% (excess heterogeneity)")
    if k < k_floor:
        reasons.append(f"k={k} below floor {k_floor} (too few studies to pool reliably)")
    suppress = bool(reasons)
    endpoint_class = classify_endpoint(outcome)
    flag = {
        "outcome": outcome, "group": r.get("group"), "kind": kind, "k": k, "i2": i2,
        "non_comparative": non_comparative,
        "endpoint_class": endpoint_class,
        "suppress_headline": suppress,
        "require_prediction_interval": suppress,
        "reasons": reasons,
    }
    if endpoint_class == "surrogate":
        flag["surrogate_caveat"] = ("Surrogate endpoint — not validated for clinical benefit "
                                    "in this setting unless cited.")
    if kind == "hazard_ratio" and indirect:
        # an anchored indirect / NMA estimate must be GRADE-downrated for indirectness
        flag["grade_indirectness_downgrade"] = True
        flag["evidence_directness"] = "indirect"
    return flag


def assess(stats: dict, *, indirect: bool = False, i2_max: float = I2_MAX,
           k_floor_prop: int = K_FLOOR_PROP, k_floor_hr: int = K_FLOOR_HR) -> dict:
    """Per pooled outcome/group, decide headline suppression, comparability, surrogate
    status, and (for indirect/NMA hazard ratios) mandatory GRADE indirectness downgrade."""
    flags: list[dict] = []
    for o in stats.get("outcomes", []) or []:                      # pooled proportions
        name = o.get("field") or o.get("label") or "outcome"
        for r in o.get("results", []) or []:
            flags.append(_flag(name, r, kind="proportion", non_comparative=True,
                               i2_max=i2_max, k_floor=k_floor_prop, indirect=indirect))
    for o in stats.get("hr_outcomes", []) or []:                   # pooled hazard ratios
        name = o.get("field") or o.get("label") or "outcome"
        for r in o.get("results", []) or []:
            flags.append(_flag(name, r, kind="hazard_ratio", non_comparative=False,
                               i2_max=i2_max, k_floor=k_floor_hr, indirect=indirect))
    return {
        "i2_max": i2_max, "k_floor_prop": k_floor_prop, "k_floor_hr": k_floor_hr,
        "indirect": indirect,
        "flags": flags,
        "n_flags": len(flags),
        "n_suppressed": sum(1 for f in flags if f["suppress_headline"]),
        "n_non_comparative": sum(1 for f in flags if f["non_comparative"]),
        "n_surrogate": sum(1 for f in flags if f["endpoint_class"] == "surrogate"),
        "n_indirect_downgraded": sum(1 for f in flags if f.get("grade_indirectness_downgrade")),
    }


def evidence_strength_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    stats = read_json(run_dir, "stats.json")
    indirect = bool(getattr(ctx.cfg, "indirect_comparison", False))
    report = assess(stats, indirect=indirect)
    path, _ = write_json(run_dir, "evidence_strength.json", report)
    return StageResult(
        outputs={"evidence_strength.json": path},
        summary={"n_flags": report["n_flags"], "n_suppressed": report["n_suppressed"],
                 "n_non_comparative": report["n_non_comparative"],
                 "n_surrogate": report["n_surrogate"],
                 "n_indirect_downgraded": report["n_indirect_downgraded"]},
        deterministic_inputs={"stats.json": _hash_of(run_dir, "stats.json")},
    )
