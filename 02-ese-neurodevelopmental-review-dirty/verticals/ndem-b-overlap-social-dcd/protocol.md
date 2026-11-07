# Protocol — ADHD, Autism, Developmental Coordination Disorder, and Adult Social Functioning

> AI-drafted protocol (rapid review, PICO framing). Every assumption the rough
> draft did not specify is marked **[NEEDS HUMAN INPUT]**. No evidence or results are asserted.
> Registration: Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.

## 1. Background & objective
This module evaluates overlap among ADHD, autism, and developmental coordination disorder and competing explanations for adult social and motor difficulties.

## 2. Review question (PICO)
| Element | Definition |
|---|---|
| **Population** | Adolescents and adults with ADHD, autism-spectrum disorder or traits, developmental coordination disorder, or overlapping profiles. |
| **Exposure/Intervention** | Single or co-occurring neurodevelopmental profiles, social-communication features, motor coordination, and developmental overlap. |
| **Comparator** | Neurotypical controls, single-diagnosis groups, or alternative psychiatric and contextual explanations. |
| **Designs** | systematic review, meta-analysis, diagnostic validation, cohort, case-control, cross-sectional, qualitative |

## 3. Eligibility criteria
**Include (all must hold):**
- Adolescent or adult sample with ADHD, autism, developmental coordination disorder, or validated traits.
- Reports overlap, differential diagnosis, social communication, peer functioning, adaptive functioning, or motor coordination.
- Provides empirical comparative data or an authoritative evidence synthesis.

**Exclude (any excludes):**
- Pediatric-only sample with no relevance to persistence beyond adolescence.
- No separable target neurodevelopmental group.
- Opinion or advocacy without empirical data.
- No outcome relevant to claims H5 or H11.

## 4. Information sources & search strategy
Databases: PubMed, Europe PMC, PsycINFO, Embase, Scopus. Date range: inception – 2026/07/29.

PubMed search string (auto-generated; verify before running):
```
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Autism Spectrum Disorder"[Mesh] OR "Developmental Coordination Disorder"[Mesh] OR ADHD[tiab] OR autism[tiab] OR autistic[tiab] OR "developmental coordination disorder"[tiab] OR dyspraxia[tiab] ) AND ( comorbidity[tiab] OR overlap[tiab] OR "differential diagnosis"[tiab] OR "social communication"[tiab] OR "social functioning"[tiab] OR peer[tiab] OR bullying[tiab] OR literal[tiab] OR "motor coordination"[tiab] OR "adaptive functioning"[tiab] ) AND ( adult[tiab] OR adolescent[tiab] OR cohort[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## 5. Study selection (Reviewer 1)
Title/abstract → full-text against §3; `include`/`exclude`/`uncertain` with a one-line reason per record. AI = Reviewer 1; an independent human Reviewer 2 pass is required.

## 6. Data extraction dictionary
Fields marked * are full-text-level (else abstract-level):

| Group | Fields |
|---|---|
| identity | claim_ids, study_design, country, funding_conflicts* |
| population | total_n, age_range, diagnostic_method, comparison_groups |
| findings | outcome_measure, effect_estimate, confidence_interval, direction, incremental_validity, supporting_quote |
| appraisal | major_limitations, alternative_explanations, directness_to_claim |

## 7. Risk of bias
Newcastle-Ottawa Scale (NOS; >=7/9 high quality).

## 8. Synthesis & statistical analysis
- **Primary:** highest-vs-lowest pooling — Narrative synthesis; no pooling across diagnostic constructs..
- **Outcomes (primary):**
- Diagnostic or trait overlap among ADHD, autism, and developmental coordination disorder  _[narrative only — no registered poolable field]_
- Social-communication, peer, adaptive, and motor outcomes  _[narrative only — no registered poolable field]_
- **Secondary outcomes:**
- Incremental explanatory value and alternative explanations
- **Heterogeneity:** Cochran Q + I².
- **Subgroups:** diagnosed conditions versus traits, age, sex or gender, single versus overlapping diagnosis.
- **Sensitivity:** leave-one-out.
- **Dose-response:** not requested.
- **Publication bias:** not requested.

## 9. Registration & honesty
Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.. AI Reviewer-1 draft; cite-or-omit; quote-anchored values; human Reviewer 2 gate required before any output is final.
