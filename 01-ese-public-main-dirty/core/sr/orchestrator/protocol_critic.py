"""Knowledge-based protocol critic (WP-M3.4, spec §3 stage 4).

A deterministic, citable pitfall checklist run against the draft protocol — NOT an LLM
(so it's transparent and reproducible). Each finding carries a rule id, a methodology
citation, a severity, and a message. The learned/ML pitfall detector is a future phase
(gated on the validation corpus); this is its knowledge-based predecessor.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from core.sr.orchestrator.adapters import _hash_of, read_json, write_json
from core.sr.orchestrator.frontend import PicoDraft
from core.sr.orchestrator.stages import RunContext, StageResult

# Short abbreviations are matched as WHOLE WORDS (so "os" doesn't match "diagnosis");
# multi-word phrases are matched as substrings (safe — no short-token collisions).
# "survival" ALONE is ambiguous (progression-free/disease-free survival are surrogates),
# so definitive survival is only the "overall survival" phrase (or OS token).
_DEF_TOKENS = {"os", "qol", "mortality"}
_DEF_PHRASES = ("overall survival", "quality of life")
_SUR_TOKENS = {"orr", "pfs", "mrd", "dor", "vgpr"}
_SUR_PHRASES = ("response rate", "progression-free", "disease-free", "event-free",
                "relapse-free", "duration of response")


def _matches(outcome_lc: str, tokens: set[str], phrases: tuple[str, ...]) -> bool:
    words = set(re.findall(r"[a-z0-9]+", outcome_lc))
    return bool(words & tokens) or any(ph in outcome_lc for ph in phrases)


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str       # blocker | should | nit
    message: str
    citation: str


def critique(pico: PicoDraft) -> list[Finding]:
    """Run the deterministic pitfall checklist over a draft PICO."""
    out: list[Finding] = []
    outcomes = [o.lower() for o in pico.outcomes]

    if not pico.outcomes:
        out.append(Finding("PC005", "blocker",
                           "No prespecified outcomes — a review must predefine its outcomes.",
                           "PRISMA 2020 item 6"))
    else:
        has_def = any(_matches(o, _DEF_TOKENS, _DEF_PHRASES) for o in outcomes)
        has_sur = any(_matches(o, _SUR_TOKENS, _SUR_PHRASES) for o in outcomes)
        if has_sur and not has_def:
            out.append(Finding("PC002", "should",
                               "Outcomes are surrogate-only (e.g. ORR/PFS/MRD) with no survival or "
                               "QoL endpoint — rate down GRADE indirectness and do not state "
                               "clinical benefit from surrogates alone.",
                               "GRADE (indirectness); surrogate-endpoint guidance"))

    if not pico.inclusion_criteria or not pico.exclusion_criteria:
        out.append(Finding("PC003", "should",
                           "Eligibility criteria incomplete — specify explicit inclusion AND "
                           "exclusion criteria.",
                           "Cochrane Handbook; PRISMA 2020 item 5"))

    if pico.framing == "PICO" and not pico.comparator.strip():
        out.append(Finding("PC001", "should",
                           "No comparator for a PICO question — confirm single-arm (descriptive) "
                           "vs comparative scope; it changes the analysis and claims you may make.",
                           "PRISMA 2020; effect-measure selection"))

    if pico.comparator.strip():
        out.append(Finding("PC006", "should",
                           "Comparator present — if the comparison is indirect / network "
                           "meta-analysis, document and check the transitivity and consistency "
                           "assumptions before pooling.",
                           "NICE DSU TSD 18"))

    if len(pico.population.strip()) < 8:
        out.append(Finding("PC004", "nit",
                           "Population description is very short — ensure it is specific "
                           "(setting, severity, and key eligibility characteristics).",
                           "PRISMA 2020 item 4"))
    return out


def protocol_critic_adapter(run_dir: Path, ctx: RunContext) -> StageResult:
    pico = PicoDraft.model_validate(read_json(run_dir, "pico.json"))
    findings = critique(pico)
    report = {
        "method": "knowledge-based deterministic checklist (no ML)",
        "findings": [asdict(f) for f in findings],
        "n_blocker": sum(1 for f in findings if f.severity == "blocker"),
        "n_should": sum(1 for f in findings if f.severity == "should"),
        "n_nit": sum(1 for f in findings if f.severity == "nit"),
    }
    path, _ = write_json(run_dir, "protocol_critique.json", report)
    return StageResult(
        outputs={"protocol_critique.json": path},
        summary={"n_findings": len(findings), "n_blocker": report["n_blocker"],
                 "rule_ids": [f.id for f in findings]},
        deterministic_inputs={"pico.json": _hash_of(run_dir, "pico.json")},
    )
