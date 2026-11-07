"""Persist the manually reviewed title/abstract decisions for NDEM module E2."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


RUN_DIR = Path("runs/ndem-rapid-20260729/ndem-e2-mood-substances")

# PMID -> (claim IDs, evidence role, priority, screening reason)
INCLUDE = {
    "38433530": (["H8.1", "H8.3", "H8.5"], "mixed", "high", "Direct systematic review of ADHD drugs in bipolar disorder with cognitive and safety outcomes."),
    "39624511": (["H8.3", "H8.6"], "supporting", "moderate", "Systematic review of methylphenidate misuse and psychiatric outcomes in a vulnerable population."),
    "41496432": (["H8.2", "H8.3", "H8.6"], "mixed", "high", "Systematic review directly examining ADHD, pharmacological/substance factors, and first-episode psychosis."),
    "42286971": (["H8.1", "H8.2", "H8.4", "H8.6"], "supporting", "high", "Systematic review of substance-specific effects on bipolar phenomenology and course."),
    "41874961": (["H8.3"], "mixed", "high", "Systematic review/meta-analysis directly comparing psychosis outcomes from therapeutic and nontherapeutic stimulants."),
    "40900605": (["H8.1", "H8.3"], "mixed", "high", "Systematic review/meta-analysis directly quantifying psychosis or bipolar disorder after stimulant exposure in ADHD."),
    "41404486": (["H8.1", "H8.5"], "mixed", "moderate", "Systematic review directly addresses medication-induced activation versus manic/hypomanic switch."),
    "42037032": (["H8.1", "H8.2"], "qualifying", "moderate", "Systematic review of substance-induced psychosis phenomenology and differential diagnosis."),
    "40343695": (["H8.3", "H8.5"], "mixed", "high", "Meta-analysis of stimulant safety across randomized trials."),
    "41127784": (["H8.2"], "mixed", "low", "Systematic review directly evaluates cannabis and psychosis-related outcomes."),
    "41561992": (["H8.2", "H8.6"], "mixed", "moderate", "Systematic review examines cannabis interactions with dissociation and first-episode psychosis."),
    "38156605": (["H8.1", "H8.3"], "mixed", "moderate", "Systematic review directly addresses stimulant-associated mania and psychosis in pediatric bipolar contexts."),
    "38670627": (["H8.1", "H8.3"], "mixed", "high", "Systematic review/meta-analysis of manic recurrence with prescribed psychostimulants in bipolar disorder."),
    "41343139": (["H8.2"], "qualifying", "low", "Scoping review includes randomized evidence and safety of medicinal cannabis in mental disorders."),
    "41448395": (["H8.1"], "qualifying", "moderate", "Systematic review characterizes mixed features relevant to episodic state differentiation."),
    "41664079": (["H8.1", "H8.2"], "supporting", "high", "Systematic review/meta-analysis of later schizophrenia-spectrum or bipolar diagnoses after cannabis-induced psychosis."),
    "41847966": (["H8.1"], "supporting", "moderate", "Large meta-analysis characterizes psychotic symptoms within bipolar disorder."),
    "41916924": (["H8.2", "H8.6"], "supporting", "high", "Systematic review directly examines psychosis risk from cannabis exposure among people with ADHD."),
    "41980405": (["H8.2", "H8.7"], "mixed", "moderate", "Systematic review addresses symptomatic, functional, cognitive, and subjective recovery after cannabis-induced psychosis."),
    "42331312": (["H8.2"], "supporting", "moderate", "Systematic review/meta-analysis examines cannabis exposure and clinical impact in early-onset psychosis."),
    "40766761": (["H8.1", "H8.5"], "supporting", "moderate", "Systematic review directly evaluates medication-induced manic and psychotic symptoms as an alternative cause."),
    "41464755": (["H8.3", "H8.5"], "mixed", "moderate", "Systematic review of adult ADHD pharmacotherapy effects on mood and substance-use comorbidity."),
    "41694044": (["H8.1", "H8.5"], "qualifying", "moderate", "Current professional guideline relevant to defining and managing bipolar states and medication effects."),
    "41872072": (["H8.2", "H8.7"], "mixed", "high", "Systematic review directly examines neurocognitive recovery following cannabis cessation."),
    "41948975": (["H8.2"], "qualifying", "low", "Meta-analysis of cannabidiol trials helps separate CBD treatment evidence from THC-associated harm claims."),
    "42100881": (["H8.1", "H8.5"], "alternative", "low", "Systematic review of manic/hypomanic states associated with a medical context relevant to alternative causation."),
    "42125461": (["H8.2"], "supporting", "high", "Systematic review directly assesses cognitive and neurodevelopmental consequences of adolescent cannabis use."),
    "42207659": (["H8.1", "H8.7"], "qualifying", "low", "Systematic review/meta-analysis links cognition with functioning in bipolar disorder and informs residual trait-like burden."),
    "42261768": (["H8.5", "H8.7"], "qualifying", "low", "Systematic review of lithium and long-term cognition informs medication and persistent-cognition alternatives."),
    "42293941": (["H8.2"], "supporting", "high", "Systematic review directly assesses neuropsychological effects of cannabis across age groups."),
    "42316899": (["H8.1"], "supporting", "moderate", "Systematic review of positive beliefs during hypomania/mania informs episodic salience and interpretation."),
    "42464611": (["H8.1"], "supporting", "moderate", "Systematic review/meta-analysis characterizes a manic-course phenotype relevant to episodic-state differentiation."),
    "41881765": (["H8.1"], "qualifying", "moderate", "Systematic review compares psychotic and nonpsychotic depressive states in unipolar and bipolar illness."),
    "40217607": (["H8.2", "H8.4", "H8.6"], "mixed", "low", "Systematic review of neuroimaging in psychiatric disorders with comorbid substance use."),
    "41856665": (["H8.1", "H8.3"], "opposing", "high", "Within-subject bipolar cohort directly compares outcomes during add-on ADHD medication exposure."),
    "41389186": (["H8.2", "H8.3"], "mixed", "moderate", "Clinical-record study directly describes stimulant and medicinal-cannabis prescribing around early psychosis."),
    "42014957": (["H8.2", "H8.6"], "qualifying", "low", "Prospective service cohort reports substance-use trajectories in youth at risk for psychosis."),
    "42262032": (["H8.1", "H8.7"], "qualifying", "low", "Bipolar study relates mania/psychosis risk dimensions to sustained-attention performance."),
    "42320288": (["H8.1", "H8.2"], "supporting", "moderate", "Observational study examines onset and progression of substance-induced psychosis."),
    "42324762": (["H8.1", "H8.7"], "qualifying", "moderate", "Longitudinal validation study tests whether bipolar screening responses vary with current mood state."),
    "40694311": (["H8.3", "H8.5"], "qualifying", "low", "Narrative safety review of methylphenidate is directly relevant but lower in the evidence hierarchy."),
    "39262211": (["H8.1", "H8.3"], "supporting", "high", "Case-control study directly evaluates dose-related incident psychosis or mania with prescription amphetamines."),
    "41320178": (["H8.1"], "qualifying", "moderate", "Longitudinal cohort compares psychotic and nonpsychotic mania and mixed features."),
    "41858652": (["H8.1", "H8.5"], "qualifying", "low", "Review explicitly considers treatment-emergent affective switching with ketamine in bipolar disorder.")
}

UNCERTAIN = {
    "42494831": (["H8.2"], "qualifying", "low", "CBD neuroimaging may qualify cannabis claims, but direct relevance to THC-associated state effects is uncertain."),
    "40732269": (["H8.1", "H8.5"], "unknown", "low", "Pediatric bipolar medication review may contain state-adverse-event data, but the abstract does not establish it."),
    "41993065": (["H8.3", "H8.5"], "unknown", "low", "Methylphenidate motor-effects review may capture acute state effects, but direct differential relevance is unclear.")
}

SPECIFIC_EXCLUDE_REASONS = {
    "41826602": "Broad addiction PET metabolism review does not address the prespecified clinical state or differential outcomes.",
    "42210342": "Cannabis treatment review in PTSD is outside the target ADHD/bipolar differential population and modifier question.",
    "41817607": "Gaming-disorder substance-use prevalence is outside the target state-modifier question.",
    "41969881": "Preclinical rodent withdrawal review is excluded from the human clinical synthesis.",
    "42396402": "Animal herbal-medicine review is preclinical and outside the exposure question.",
    "42506336": "Gut-microbiota review does not report the prespecified clinical cognitive, manic, or psychotic outcomes.",
    "39448224": "Protocol only; no results available.",
    "41945549": "Meta-analysis protocol only; no results available.",
    "39856692": "Systematic-review protocol only; no results available.",
    "41875420": "Prospective cohort protocol only; no results available.",
    "40457330": "Sleep/postpartum-psychosis study belongs to the sleep module rather than E2.",
    "42512516": "Sleep meta-analysis belongs to module E1 rather than mood/substance exposure module E2.",
    "42010928": "Prenatal nicotine evidence is not an acute nicotine state-modifier study and is largely preclinical.",
    "40892018": "Prenatal vaping exposure is outside the adult/adolescent state-modifier question.",
    "42400661": "Prenatal cannabinoid exposure is outside the prespecified adolescent/adult state-modifier question.",
    "41083615": "Pregnancy medication outcomes are outside the state/differential outcomes prespecified for this module.",
    "41782788": "Small illicit-methamphetamine treatment case series is outside prescribed-stimulant exposure and cannot estimate risk.",
}


def main() -> int:
    candidates = json.loads((RUN_DIR / "screening_candidates.json").read_text())
    candidate_pmids = {row["pmid"] for row in candidates}
    mapped = set(INCLUDE) | set(UNCERTAIN)
    missing_mapped = mapped - candidate_pmids
    if missing_mapped:
        raise ValueError(f"Mapped PMIDs not in candidate set: {sorted(missing_mapped)}")

    rows = []
    for record in candidates:
        pmid = record["pmid"]
        abstract_available = bool(
            record.get("abstract")
            and record["abstract"] != "[no abstract available]"
        )
        if pmid in INCLUDE:
            claims, role, priority, reason = INCLUDE[pmid]
            decision = "include"
        elif pmid in UNCERTAIN:
            claims, role, priority, reason = UNCERTAIN[pmid]
            decision = "uncertain"
        else:
            claims, role, priority = [], "unknown", "low"
            decision = "exclude"
            reason = SPECIFIC_EXCLUDE_REASONS.get(
                pmid,
                "Does not directly evaluate a prespecified mood/substance state modifier, relevant outcome, or differential explanation.",
            )
        rows.append(
            {
                "pmid": pmid,
                "module": "ndem-e2-mood-substances",
                "decision": decision,
                "reason": reason,
                "claim_ids": claims,
                "evidence_role": role,
                "priority": priority,
                "abstract_available": abstract_available,
                "fulltext_required": decision != "exclude",
            }
        )

    if len(rows) != len(candidates) or {r["pmid"] for r in rows} != candidate_pmids:
        raise AssertionError("Screening output does not exactly cover candidates.")
    (RUN_DIR / "screening_ai_r1.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    counts = Counter(row["decision"] for row in rows)
    high = [
        row["pmid"]
        for row in rows
        if row["decision"] != "exclude" and row["priority"] == "high"
    ]
    summary = (
        "# AI Reviewer-1 screening — E2 mood/substance modifiers\n\n"
        f"- Candidates screened: {len(rows)}\n"
        f"- Include: {counts['include']}\n"
        f"- Exclude: {counts['exclude']}\n"
        f"- Uncertain: {counts['uncertain']}\n"
        f"- High-priority include/uncertain PMIDs: {', '.join(high)}\n\n"
        "This is a title/abstract-level AI Reviewer-1 pass. Human Reviewer 2 "
        "screening and clinical sign-off remain pending.\n"
    )
    (RUN_DIR / "screening_ai_r1_summary.md").write_text(summary, encoding="utf-8")
    print(json.dumps(dict(counts), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
