# Protocol — DSM-5 Neurodevelopmental Criteria, Mechanisms, and Supports

**Version:** 1.0.0  
**Profile:** systematic baseline followed by living updates  
**Status:** Draft; freeze and prospective registration required before screening

## 1. Objective

Evaluate each mapped DSM-5 criterion for construct validity, diagnostic discrimination, functional relevance, developmental stability, and context dependence. Then map human and preclinical mechanisms and evaluate interventions or supports without conflating mechanism with efficacy.

The target diagnoses are ADHD, Autism Spectrum Disorder, Specific Learning Disorder (reading, written expression, mathematics), and Developmental Coordination Disorder. A transdiagnostic shared-domain stream examines overlap without treating overlap as diagnostic equivalence.

## 2. Review question

| Element | Definition |
|---|---|
| Population | Lifespan samples with a target diagnosis, validated traits, or an appropriate comparison group |
| Validation exposure | A mapped criterion, diagnostic construct, instrument, informant, or shared domain |
| Mechanism exposure | A prespecified cognitive, social, language, motor, sensory, arousal, emotional, or environmental mechanism |
| Intervention exposure | Medication, psychotherapy/behavioral, education/OT/environmental, assistive/digital technology, or lifestyle/sleep/participation support |
| Comparators | Alternative diagnoses, neurotypical controls, comorbid/single-diagnosis groups, usual care, active controls, baseline, or no exposure |
| Primary outcomes | Construct validity, diagnostic discrimination, impairment/participation association, mechanism evidence, symptom effects, and function effects |

## 3. Staged architecture

1. Freeze criterion crosswalk and controlled vocabulary.
2. Run four diagnosis-specific validation streams.
3. Run the shared-domain overlap stream; add pairwise analyses only when justified.
4. Run mechanism synthesis, stratified by evidence level.
5. Run intervention synthesis with established, experimental, off-label, and unclear status separated.
6. Produce a baseline snapshot, then quarterly living deltas.

## 4. Eligibility

Include empirical human studies, systematic reviews, meta-analyses, guidelines, and trial registries with a separable mapped criterion, domain, mechanism, intervention, or harm outcome. Preclinical evidence may be retained only in the labelled mechanism map and cannot support clinical validity or treatment claims.

Exclude opinion without evidence, irrelevant or non-separable samples, duplicates without new information, and records without a mapped outcome.

## 5. Information sources

PubMed/MEDLINE, Europe PMC, PsycINFO, Embase, Cochrane CENTRAL, ERIC, Scopus, ClinicalTrials.gov, WHO-ICTRP, guideline sources, and backward/forward citation chaining. The search manifest must record database, platform, date, query, limits, export, and count.

## 6. Screening and extraction

Codex performs AI Reviewer-1 draft screening and extraction. Claude performs a separate secondary-AI cross-check. Every AI decision has a reason, provider/model provenance, timestamp, source provenance, and uncertainty flag. A named human expert then independently reviews screening, full text, extraction, and risk of bias as Reviewer 2; reconciliation is recorded separately. Neither AI review satisfies the human Reviewer 2 gate.

The extraction schema keeps diagnostic validation, overlap, mechanism, intervention, symptom, function, participation, quality of life, harms, and durability in separate fields. Values not stated in the source are `NR`; inferred values are `ESTIMATED:` with method and quote.

## 7. Risk of bias and certainty

Use design-appropriate tools: RoB 2 for randomized trials, ROBINS-I for non-randomized interventions/exposures, QUADAS-2 for diagnostic accuracy, cross-sectional appraisal for prevalence/association studies, qualitative appraisal where relevant, and AMSTAR 2 for reviews. Tool-specific judgment polarity is mandatory. Certainty is assessed per evidence stream, diagnosis/domain, criterion, outcome, and age stratum; it is not a single certainty score for a diagnosis. Missing full text or risk-of-bias assessment forces a Very Low/No-data state rather than silently contributing to certainty.

## 8. Synthesis rules

- Narrative synthesis is the default.
- Pool only compatible construct, population, design, comparator, instrument, outcome, and time-point combinations.
- Separate association, mechanism, and improvement language.
- Report symptom and function outcomes separately.
- Apply multiplicity control for supported pairwise overlap analyses.
- Do not classify absent evidence as evidence against a criterion.

## 9. Governance gates

The public/clinical package remains locked until Codex AI Reviewer 1, Claude secondary-AI review, independent human Reviewer 2, reconciliation, and qualified clinical sign-off are separately recorded. AI reviewer completion never satisfies the human gate. The package must show supported, intermediate, indeterminate, and rejected claims, plus what each claim does not show. Rejected claims require an explicit falsification rule and cannot be inferred from missing evidence.

## 10. Living updates

Run quarterly searches and retain delta artifacts. New verified records may update a claim only through an explicit change log with protocol version, prior status, new status, reason, and provenance. No silent overwrite is permitted.
