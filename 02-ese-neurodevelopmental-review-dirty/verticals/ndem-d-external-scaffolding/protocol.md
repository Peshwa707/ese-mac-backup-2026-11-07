# Protocol — External Cognitive Scaffolding for ADHD and Executive Dysfunction

> AI-drafted protocol (rapid review, PICO framing). Every assumption the rough
> draft did not specify is marked **[NEEDS HUMAN INPUT]**. No evidence or results are asserted.
> Registration: Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.

## 1. Background & objective
This module evaluates coaching, executive-function-focused CBT, organizational-skills interventions, checklists, reminders, digital aids, assistive technology, and occupational redesign, including durability and harms.

## 2. Review question (PICO)
| Element | Definition |
|---|---|
| **Population** | Adolescents and adults with ADHD or clinically meaningful executive dysfunction. |
| **Exposure/Intervention** | Coaching, executive-function-focused CBT, checklists, reminders, organizational-skills training, digital aids, assistive technology, structured deadlines, or occupational/educational redesign. |
| **Comparator** | Usual care, waitlist, active control, alternative intervention, or pre-intervention baseline. |
| **Designs** | systematic review, meta-analysis, randomized controlled trial, controlled trial, prospective cohort, qualitative |

## 3. Eligibility criteria
**Include (all must hold):**
- Adolescent or adult ADHD or executive-dysfunction population.
- Evaluates an external scaffold or executive-function support intervention.
- Includes a functional, performance, quality-of-life, durability, acceptability, or harm outcome.
- Empirical intervention study or authoritative evidence synthesis.

**Exclude (any excludes):**
- Medication-only intervention without an external scaffold.
- No separable ADHD or executive-dysfunction population.
- Protocol, opinion, or commercial treatment claim without empirical data.
- No outcome relevant to claim H4.

## 4. Information sources & search strategy
Databases: PubMed, Europe PMC, ClinicalTrials.gov, PsycINFO, Embase, CENTRAL. Date range: inception – 2026/07/29.

PubMed search string (auto-generated; verify before running):
```
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR ADHD[tiab] OR "executive dysfunction"[tiab] ) AND ( coaching[tiab] OR "cognitive behavioral therapy"[tiab] OR "organizational skills"[tiab] OR checklist[tiab] OR reminder[tiab] OR "digital intervention"[tiab] OR "assistive technology"[tiab] OR "cognitive aid"[tiab] OR "external scaffold"[tiab] OR "occupational redesign"[tiab] OR "educational accommodation"[tiab] OR "artificial intelligence"[tiab] ) AND ( randomized[tiab] OR randomised[tiab] OR trial[tiab] OR controlled[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## 5. Study selection (Reviewer 1)
Title/abstract → full-text against §3; `include`/`exclude`/`uncertain` with a one-line reason per record. AI = Reviewer 1; an independent human Reviewer 2 pass is required.

## 6. Data extraction dictionary
Fields marked * are full-text-level (else abstract-level):

| Group | Fields |
|---|---|
| identity | claim_ids, study_design, country, funding_conflicts*, registration |
| population | total_n, age_range, diagnostic_method |
| intervention | intervention_type, duration, comparator |
| findings | outcome_measure, effect_estimate, confidence_interval, follow_up, harms, direction, supporting_quote |
| appraisal | major_limitations, alternative_explanations, directness_to_claim |

## 7. Risk of bias
Cochrane RoB 2.0 (RCTs) / ROBINS-I (non-randomized) — auto-selected by design.

## 8. Synthesis & statistical analysis
- **Primary:** highest-vs-lowest pooling — Intervention-specific narrative synthesis; pool only homogeneous outcomes after human review..
- **Outcomes (primary):**
- Real-world task completion, omissions, functioning, or quality of life  _[narrative only — no registered poolable field]_
- Durability, acceptability, dependence, and harms  _[narrative only — no registered poolable field]_
- **Secondary outcomes:**
- ADHD symptoms, adherence, academic or occupational performance, and evidence for AI assistance
- **Heterogeneity:** Cochran Q + I².
- **Subgroups:** intervention type, age, diagnostic method, objective versus self-report outcome, follow-up duration.
- **Sensitivity:** leave-one-out.
- **Dose-response:** not requested.
- **Publication bias:** not requested.

## 9. Registration & honesty
Protocol frozen for AI-assisted rapid review on 2026-07-29; not prospectively registered.. AI Reviewer-1 draft; cite-or-omit; quote-anchored values; human Reviewer 2 gate required before any output is final.
