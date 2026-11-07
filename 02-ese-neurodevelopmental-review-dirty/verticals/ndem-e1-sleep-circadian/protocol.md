# Protocol — Sleep and Circadian Modifiers of ADHD and Bipolar-Spectrum Functioning

> AI-drafted protocol (rapid review, PICO framing). Every assumption the rough
> draft did not specify is marked **[NEEDS HUMAN INPUT]**. No evidence or results are asserted.
> Registration: Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.

## 1. Background & objective
This module evaluates whether sleep duration, sleep quality, circadian irregularity, and shift work modify attention, executive functioning, irritability, mood, and mania risk.

## 2. Review question (PICO)
| Element | Definition |
|---|---|
| **Population** | Adolescents and adults with ADHD, bipolar-spectrum disorder, both conditions, or relevant control groups. |
| **Exposure/Intervention** | Short sleep, sleep deprivation, insomnia, circadian disruption, irregular sleep, delayed sleep phase, or shift work. |
| **Comparator** | Adequate or regular sleep, euthymia, non-shift work, within-person baseline, or unexposed controls. |
| **Designs** | systematic review, meta-analysis, longitudinal cohort, within-person, experimental sleep deprivation, prospective, cross-sectional |

## 3. Eligibility criteria
**Include (all must hold):**
- Adolescent or adult ADHD or bipolar-spectrum sample, or a study directly relevant to differential diagnosis.
- Measures sleep duration, quality, circadian timing, irregularity, insomnia, or shift work.
- Reports cognitive, functional, mood, mania, psychosis, or irritability outcomes.
- Empirical study or authoritative evidence synthesis.

**Exclude (any excludes):**
- Sleep-treatment efficacy study without information on sleep as a modifier.
- No separable ADHD or bipolar-spectrum result and no direct differential-diagnosis relevance.
- Opinion or protocol without empirical results.
- No outcome relevant to claim H7.

## 4. Information sources & search strategy
Databases: PubMed, Europe PMC, ClinicalTrials.gov, PsycINFO, Embase, Web of Science. Date range: inception – 2026/07/29.

PubMed search string (auto-generated; verify before running):
```
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Bipolar Disorder"[Mesh] OR ADHD[tiab] OR "bipolar disorder"[tiab] OR mania[tiab] OR hypomania[tiab] ) AND ( "Sleep Deprivation"[Mesh] OR "Circadian Rhythm Sleep Disorders"[Mesh] OR sleep[tiab] OR insomnia[tiab] OR circadian[tiab] OR "sleep deprivation"[tiab] OR "sleep duration"[tiab] OR "sleep quality"[tiab] OR "shift work"[tiab] OR "delayed sleep phase"[tiab] ) AND ( attention[tiab] OR "executive function"[tiab] OR cognition[tiab] OR irritability[tiab] OR mood[tiab] OR mania[tiab] OR psychosis[tiab] OR longitudinal[tiab] OR prospective[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## 5. Study selection (Reviewer 1)
Title/abstract → full-text against §3; `include`/`exclude`/`uncertain` with a one-line reason per record. AI = Reviewer 1; an independent human Reviewer 2 pass is required.

## 6. Data extraction dictionary
Fields marked * are full-text-level (else abstract-level):

| Group | Fields |
|---|---|
| identity | claim_ids, study_design, country, funding_conflicts* |
| population | total_n, age_range, diagnostic_method, mood_state |
| exposure | sleep_measure, sleep_exposure, timing |
| findings | outcome_measure, effect_estimate, confidence_interval, temporal_direction, adjusted_covariates*, direction, supporting_quote |
| appraisal | major_limitations, reverse_causation, directness_to_claim |

## 7. Risk of bias
Newcastle-Ottawa Scale (NOS; >=7/9 high quality).

## 8. Synthesis & statistical analysis
- **Primary:** highest-vs-lowest pooling — Narrative synthesis separating trait, state, and temporal-direction evidence..
- **Outcomes (primary):**
- Attention, executive function, irritability, and real-world functioning  _[narrative only — no registered poolable field]_
- Hypomanic, manic, mixed, or psychotic symptoms and mood instability  _[narrative only — no registered poolable field]_
- **Secondary outcomes:**
- Temporal direction, within-person effects, and reverse causation
- **Heterogeneity:** Cochran Q + I².
- **Subgroups:** ADHD versus bipolar disorder, objective versus self-report sleep, within-person versus between-person, age, mood state.
- **Sensitivity:** leave-one-out.
- **Dose-response:** not requested.
- **Publication bias:** not requested.

## 9. Registration & honesty
Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.. AI Reviewer-1 draft; cite-or-omit; quote-anchored values; human Reviewer 2 gate required before any output is final.
