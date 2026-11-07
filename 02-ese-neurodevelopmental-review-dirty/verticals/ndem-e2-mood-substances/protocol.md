# Protocol — Mood and Substance State Modifiers in ADHD and Bipolar-Spectrum Differential Diagnosis

> AI-drafted protocol (rapid review, PICO framing). Every assumption the rough
> draft did not specify is marked **[NEEDS HUMAN INPUT]**. No evidence or results are asserted.
> Registration: Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.

## 1. Background & objective
This module evaluates episodic mood effects and the cognitive, psychotic, activation, and mood-stability effects of cannabis/THC, prescription stimulants, and nicotine in ADHD and bipolar-spectrum contexts.

## 2. Review question (PICO)
| Element | Definition |
|---|---|
| **Population** | Adolescents and adults with ADHD, bipolar-spectrum disorder, both conditions, substance exposure, or relevant control groups. |
| **Exposure/Intervention** | Hypomanic, manic, mixed, or psychotic state; cannabis or THC; prescribed stimulant; nicotine; medication change; or interactions among these exposures. |
| **Comparator** | Euthymia, abstinence, placebo, adequate sleep, unexposed controls, or within-person baseline. |
| **Designs** | guideline, systematic review, meta-analysis, randomized controlled trial, longitudinal cohort, case-control, within-person, pharmacovigilance |

## 3. Eligibility criteria
**Include (all must hold):**
- Adolescent or adult sample relevant to ADHD, bipolar-spectrum illness, cannabis/THC, prescription stimulants, or nicotine.
- Reports cognitive, activation, manic, psychotic, speech, salience, impulse-control, or functional outcomes.
- Provides empirical results or an authoritative professional guideline/evidence synthesis.

**Exclude (any excludes):**
- Non-prescription stimulant exposure with no relevance to prescribed ADHD treatment or bipolar differential diagnosis.
- No separable exposure or outcome relevant to claim H8.
- Opinion, commercial treatment page, protocol, or unsourced narrative.
- Case report retained only as a safety signal, not as causal or incidence evidence.

## 4. Information sources & search strategy
Databases: PubMed, Europe PMC, ClinicalTrials.gov, PsycINFO, Embase, Cochrane Library. Date range: inception – 2026/07/29.

PubMed search string (auto-generated; verify before running):
```
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Bipolar Disorder"[Mesh] OR "Cannabis"[Mesh] OR "Central Nervous System Stimulants"[Mesh] OR "Nicotine"[Mesh] OR ADHD[tiab] OR "bipolar disorder"[tiab] OR mania[tiab] OR hypomania[tiab] OR cannabis[tiab] OR THC[tiab] OR marijuana[tiab] OR methylphenidate[tiab] OR amphetamine[tiab] OR stimulant[tiab] OR nicotine[tiab] ) AND ( psychosis[tiab] OR mania[tiab] OR hypomania[tiab] OR "mood destabilization"[tiab] OR activation[tiab] OR impulsivity[tiab] OR salience[tiab] OR "rapid speech"[tiab] OR cognition[tiab] OR "executive function"[tiab] OR irritability[tiab] ) AND ( guideline[tiab] OR longitudinal[tiab] OR cohort[tiab] OR trial[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## 5. Study selection (Reviewer 1)
Title/abstract → full-text against §3; `include`/`exclude`/`uncertain` with a one-line reason per record. AI = Reviewer 1; an independent human Reviewer 2 pass is required.

## 6. Data extraction dictionary
Fields marked * are full-text-level (else abstract-level):

| Group | Fields |
|---|---|
| identity | claim_ids, study_design, country, funding_conflicts* |
| population | total_n, age_range, diagnostic_method, baseline_mood_state |
| exposure | exposure, dose, timing, coexposures, sleep_status |
| findings | outcome_measure, effect_estimate, confidence_interval, absolute_risk, temporal_direction, adjusted_covariates*, direction, supporting_quote |
| appraisal | major_limitations, confounding_by_indication, alternative_explanations, directness_to_claim |

## 7. Risk of bias
Newcastle-Ottawa Scale (NOS; >=7/9 high quality).

## 8. Synthesis & statistical analysis
- **Primary:** highest-vs-lowest pooling — Narrative safety and differential-diagnosis synthesis; no cross-exposure pooling..
- **Outcomes (primary):**
- Attention, executive function, speech, irritability, salience, impulsivity, psychosis, and mood instability  _[narrative only — no registered poolable field]_
- Evidence distinguishing lifelong traits from episodic state effects  _[narrative only — no registered poolable field]_
- **Secondary outcomes:**
- Dose, timing, interaction, confounding, and vulnerability factors
- **Heterogeneity:** Cochran Q + I².
- **Subgroups:** ADHD versus bipolar disorder, mood state, substance or medication, dose and timing, sleep status, psychosis or mania vulnerability.
- **Sensitivity:** leave-one-out.
- **Dose-response:** not requested.
- **Publication bias:** not requested.

## 9. Registration & honesty
Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.. AI Reviewer-1 draft; cite-or-omit; quote-anchored values; human Reviewer 2 gate required before any output is final.
