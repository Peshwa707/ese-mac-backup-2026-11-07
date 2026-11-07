# Protocol — Developmental Persistence, Executive Function, and Intellectual Compensation in ADHD

> AI-drafted protocol (rapid review, PICO framing). Every assumption the rough
> draft did not specify is marked **[NEEDS HUMAN INPUT]**. No evidence or results are asserted.
> Registration: Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.

## 1. Background & objective
This module evaluates persistence of ADHD-related executive and functional impairment and whether intelligence or achievement may compensate for, mask, or delay recognition of impairment.

## 2. Review question (PICO)
| Element | Definition |
|---|---|
| **Population** | Adolescents and adults with diagnosed ADHD or validated ADHD traits, including high-intelligence or high-achievement subgroups. |
| **Exposure/Intervention** | Developmental persistence, executive dysfunction, intelligence, achievement, compensation, masking, or delayed recognition. |
| **Comparator** | Controls without ADHD, lower-intelligence or lower-achievement groups, remitted ADHD, or within-person developmental follow-up. |
| **Designs** | systematic review, meta-analysis, longitudinal cohort, prospective, retrospective, cross-sectional |

## 3. Eligibility criteria
**Include (all must hold):**
- Adolescent or adult ADHD population, or developmental follow-up into adulthood.
- Evaluates persistence, executive/functional outcomes, intelligence, achievement, compensation, masking, or recognition.
- Uses a defined diagnostic or validated trait measure.
- Reports empirical results or an authoritative evidence synthesis.

**Exclude (any excludes):**
- Pediatric-only report without developmental relevance beyond adolescence.
- No separable ADHD population or validated ADHD-trait analysis.
- Opinion, advocacy, protocol, or unsourced narrative without systematic empirical data.
- No outcome relevant to claims H1 or H2.

## 4. Information sources & search strategy
Databases: PubMed, Europe PMC, PsycINFO, Embase, Web of Science. Date range: inception – 2026/07/29.

PubMed search string (auto-generated; verify before running):
```
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR ADHD[tiab] OR "attention deficit hyperactivity disorder"[tiab] ) AND ( persistence[tiab] OR longitudinal[tiab] OR follow-up[tiab] OR "executive function"[tiab] OR "functional impairment"[tiab] OR intelligence[tiab] OR gifted[tiab] OR achievement[tiab] OR compensation[tiab] OR masking[tiab] ) AND ( "Longitudinal Studies"[Mesh] OR adult[tiab] OR adolescent[tiab] OR cohort[tiab] OR prospective[tiab] OR retrospective[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## 5. Study selection (Reviewer 1)
Title/abstract → full-text against §3; `include`/`exclude`/`uncertain` with a one-line reason per record. AI = Reviewer 1; an independent human Reviewer 2 pass is required.

## 6. Data extraction dictionary
Fields marked * are full-text-level (else abstract-level):

| Group | Fields |
|---|---|
| identity | claim_ids, study_design, country, funding_conflicts* |
| population | total_n, age_range, diagnostic_method, intelligence_or_achievement_definition |
| findings | outcome_measure, effect_estimate, confidence_interval, direction, adjusted_covariates*, supporting_quote |
| appraisal | major_limitations, alternative_explanations, directness_to_claim |

## 7. Risk of bias
Newcastle-Ottawa Scale (NOS; >=7/9 high quality).

## 8. Synthesis & statistical analysis
- **Primary:** highest-vs-lowest pooling — Narrative synthesis; no cross-construct pooling..
- **Outcomes (primary):**
- Persistence of symptoms or executive/functional impairment  _[narrative only — no registered poolable field]_
- Evidence for or against intelligence-related compensation or delayed recognition  _[narrative only — no registered poolable field]_
- **Secondary outcomes:**
- Cross-setting impairment, effort, instability, quality of life, and alternative explanations
- **Heterogeneity:** Cochran Q + I².
- **Subgroups:** age, diagnostic method, intelligence or achievement, objective versus self-report outcome.
- **Sensitivity:** leave-one-out.
- **Dose-response:** not requested.
- **Publication bias:** not requested.

## 9. Registration & honesty
Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.. AI Reviewer-1 draft; cite-or-omit; quote-anchored values; human Reviewer 2 gate required before any output is final.
