"""Profile → orchestrator stage mapping."""

from __future__ import annotations

from core.sr.profile import load_profile

_ALL = frozenset({"systematic", "scoping", "rapid", "living"})

# Profile phase names (profiles.yaml) → orchestrator stage names.
_PHASE_TO_STAGES: dict[str, tuple[str, ...]] = {
    "screen": ("search", "prescreen", "screen"),
    "fulltext": ("fulltext",),
    "extract": ("extract",),
    "rob": ("rob",),
    "grade": ("grade",),
    "meta": ("stats", "evidence_strength"),
    "chart": (),  # scoping charting — manuscript layer only today
    "prospero": ("manuscript",),
    "narrative": ("narrative",),
}

_FRONTEND = frozenset({
    "idea_to_pico", "novelty_check", "feasibility_scope", "protocol_critic", "autoconfig",
})

_PIPELINE_ALWAYS = frozenset({"assemble", "manuscript"})


def profiles_for_stage(stage_name: str) -> frozenset[str]:
    """Which review profiles include this stage."""
    if stage_name in _FRONTEND:
        return _ALL
    if stage_name in _PIPELINE_ALWAYS:
        return _ALL
    out: set[str] = set()
    for profile in _ALL:
        prof = load_profile(profile)
        for phase, stages in _PHASE_TO_STAGES.items():
            if prof.enabled(phase) and stage_name in stages:
                out.add(profile)
    if not out and stage_name in ("search", "prescreen", "screen", "fulltext", "extract",
                                  "rob", "grade", "stats", "evidence_strength"):
        # Fallback: stage unknown to profiles.yaml — include all to avoid silent drops.
        return _ALL
    return frozenset(out) if out else _ALL
