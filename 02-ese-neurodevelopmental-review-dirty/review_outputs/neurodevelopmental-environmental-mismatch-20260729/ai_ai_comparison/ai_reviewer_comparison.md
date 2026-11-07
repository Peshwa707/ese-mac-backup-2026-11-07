# Codex–Claude secondary AI reviewer comparison

> Claude served as a blinded secondary AI reviewer under the same frozen rapid-review protocol. This is an AI-AI comparison, not Human Reviewer 2 screening, full-text adjudication, formal risk-of-bias/GRADE completion, treating-clinician sign-off, diagnosis, treatment guidance, or authorization to unlock conclusions. All disagreements remain pending human adjudication.

## Capability conclusion

The run demonstrates a blinded, cross-model secondary AI reviewer workflow on the same 980 ordered title/abstract occurrences and the same 66-claim protocol. It demonstrates reproducible AI-AI agreement measurement and disagreement routing. It does not demonstrate human-review equivalence, clinical validity, or adjudicated accuracy.

- The pre-comparison scientific seal and every file in its manifest were verified; seal SHA-256: `66532ac1f644a4d141894c159dc999aa2fb5bbd662e3b7a641db9a99af7164e7`.
- AI Reviewer 1 was produced in the OpenAI Codex task environment; the exact served OpenAI model identifier was not embedded in the pre-existing review artifacts and is therefore a provenance limitation.
- AI Reviewer 2 provider logs record Anthropic `claude-opus-5` as the primary scientific model, with any auxiliary model usage retained separately.

## Screening agreement

- Primary analysis: **980** ordered occurrences paired by module and occurrence position.
- Unique-within-module sensitivity: **979** module–PMID pairs after excluding **1** duplicate occurrence (F PMID 42424684 at positions 112/113).
- Three-way exact agreement: **78.8%**.
- Nominal `llm_llm_agreement` Cohen kappa: **0.533**.
- Linear-weighted `llm_llm_agreement` kappa: **0.600**.
- Advance (include/uncertain) versus exclude agreement: **82.6%**.
- Any three-way disagreements: **208**.
- Hard include-versus-exclude conflicts: **93**.
- Mean claim-ID annotation Jaccard among nonempty-union pairs (n=375): **0.379**.
- Both reviewers supplied no claim-ID annotation for **605** pairs (61.7%); these null/null pairs are not scored as perfect Jaccard agreement.
- These are descriptive AI-AI metrics, not sensitivity, specificity, accuracy, or inter-rater reliability against a human reference standard.

| Module | R1 I/U/E | R2 I/U/E | Exact | Nominal kappa | Hard conflicts |
|---|---:|---:|---:|---:|---:|
| A | 34/2/104 | 29/8/103 | 80.7% | 0.521 | 17 |
| B | 37/2/101 | 39/12/89 | 70.7% | 0.372 | 29 |
| C | 12/2/126 | 11/10/119 | 90.7% | 0.591 | 3 |
| D | 26/8/106 | 33/22/85 | 79.3% | 0.575 | 7 |
| E1 | 35/8/97 | 27/34/79 | 76.4% | 0.569 | 5 |
| E2 | 44/3/93 | 45/21/74 | 80.7% | 0.646 | 7 |
| F | 18/2/120 | 44/11/85 | 72.9% | 0.380 | 25 |

## Evidence-selection overlap

- Reviewer-1 unique module–PMID pairs: 92.
- Reviewer-2 unique module–PMID pairs: 98.
- Shared module–PMID pairs: 59.
- Pooled module–PMID Jaccard overlap: 0.450.
- Reviewer-1 module–PMID pairs excluded because they were outside the frozen candidate corpus: 4.

## Claim-level agreement

- Claims paired: **66**.
- Verdict exact agreement: **78.8%**.
- Verdict nominal `llm_llm_agreement` kappa: **0.627**.
- Verdict linear-weighted `llm_llm_agreement` kappa: **0.676**.
- Certainty exact agreement: **62.1%**.
- Directness-to-case exact agreement: **3.0%**.
- Mean per-claim module–PMID occurrence Jaccard among nonempty-union claims (n=65): **0.343**.
- Both reviewers had no frozen-corpus PMID evidence for **1** claims (1.5%).
- Material verdict disagreements routed to human adjudication: **9**.

## Material claim disagreements

| Claim | Reviewer 1 | Claude Reviewer 2 | R1 module–PMID evidence | R2 module–PMID evidence |
|---|---|---|---|---|
| H1.2 | partially supported | supported | ndem-a-persistence-compensation:40801243;ndem-a-persistence-compensation:41716858 | ndem-a-persistence-compensation:40287009;ndem-a-persistence-compensation:40801243;ndem-a-persistence-compensation:41755570;ndem-a-persistence-compensation:41985649;ndem-a-persistence-compensation:42336134;ndem-a-persistence-compensation:42365682;ndem-a-persistence-compensation:42481172 |
| H2.4 | partially supported | supported | ndem-a-persistence-compensation:41089022;ndem-a-persistence-compensation:42052784 | ndem-a-persistence-compensation:40287009;ndem-a-persistence-compensation:41989410;ndem-a-persistence-compensation:42052784 |
| H5.4 | partially supported | supported | ndem-b-overlap-social-dcd:41348918;ndem-b-overlap-social-dcd:41388592;ndem-b-overlap-social-dcd:41579913;ndem-b-overlap-social-dcd:41670017;ndem-b-overlap-social-dcd:41890162;ndem-b-overlap-social-dcd:41936228;ndem-b-overlap-social-dcd:42318259;ndem-b-overlap-social-dcd:42319023 | ndem-b-overlap-social-dcd:41121040;ndem-b-overlap-social-dcd:41388592;ndem-b-overlap-social-dcd:41579913;ndem-b-overlap-social-dcd:41638598;ndem-b-overlap-social-dcd:41989410;ndem-b-overlap-social-dcd:42319023;ndem-b-overlap-social-dcd:42508582 |
| H4.2 | partially supported | supported | ndem-d-external-scaffolding:27163988;ndem-d-external-scaffolding:28088557;ndem-d-external-scaffolding:28398986;ndem-d-external-scaffolding:32954909;ndem-d-external-scaffolding:36009086;ndem-d-external-scaffolding:40761448;ndem-d-external-scaffolding:41741588;ndem-d-external-scaffolding:41936228 | ndem-d-external-scaffolding:27163988;ndem-d-external-scaffolding:28088557;ndem-d-external-scaffolding:32954909;ndem-d-external-scaffolding:38084075;ndem-d-external-scaffolding:41741588;ndem-d-external-scaffolding:41936228 |
| H7.N1 | supported | partially supported | ndem-e1-sleep-circadian:40706099;ndem-e1-sleep-circadian:41262297;ndem-e1-sleep-circadian:41517697;ndem-e1-sleep-circadian:41618215;ndem-e1-sleep-circadian:41644956;ndem-e1-sleep-circadian:41822684;ndem-e1-sleep-circadian:41870441;ndem-e1-sleep-circadian:42320790;ndem-e1-sleep-circadian:42522250 | ndem-e1-sleep-circadian:40706099;ndem-e1-sleep-circadian:41397339;ndem-e1-sleep-circadian:41517697;ndem-e1-sleep-circadian:41644956;ndem-e1-sleep-circadian:41646692;ndem-e1-sleep-circadian:41803808;ndem-e1-sleep-circadian:41822684;ndem-e1-sleep-circadian:41870441;ndem-e1-sleep-circadian:42297780;ndem-e1-sleep-circadian:42320790;ndem-e1-sleep-circadian:42464599;ndem-e1-sleep-circadian:42512516 |
| H8.1 | supported | partially supported | ndem-e2-mood-substances:41664079;ndem-e2-mood-substances:42316899 | ndem-e2-mood-substances:41404486;ndem-e2-mood-substances:42262032;ndem-e2-mood-substances:42286971;ndem-e2-mood-substances:42512516 |
| H8.4 | partially supported | indeterminate | none | ndem-e2-mood-substances:39624511;ndem-e2-mood-substances:42286971 |
| G3 | indeterminate | unsupported | ndem-a-persistence-compensation:42365682;ndem-d-external-scaffolding:40761448;ndem-e1-sleep-circadian:42320790 | ndem-a-persistence-compensation:41989410;ndem-a-persistence-compensation:42365682;ndem-b-overlap-social-dcd:41121040;ndem-b-overlap-social-dcd:41388592;ndem-b-overlap-social-dcd:41638598;ndem-b-overlap-social-dcd:41989410;ndem-b-overlap-social-dcd:42319023;ndem-b-overlap-social-dcd:42508582;ndem-c-context-motivation-strengths:40287643;ndem-e2-mood-substances:41404486;ndem-e2-mood-substances:41916924;ndem-e2-mood-substances:42262032;ndem-f-daydreaming-rejection:41789525;ndem-f-daydreaming-rejection:41933711;ndem-f-daydreaming-rejection:42341946 |
| G5 | partially supported | supported | none | ndem-a-persistence-compensation:40801243;ndem-a-persistence-compensation:41989410;ndem-a-persistence-compensation:42365682;ndem-b-overlap-social-dcd:41121040;ndem-b-overlap-social-dcd:41388592;ndem-b-overlap-social-dcd:41579913;ndem-b-overlap-social-dcd:41638598;ndem-b-overlap-social-dcd:41989410;ndem-b-overlap-social-dcd:42120904;ndem-b-overlap-social-dcd:42319023;ndem-b-overlap-social-dcd:42508582;ndem-c-context-motivation-strengths:40287643;ndem-e2-mood-substances:41404486;ndem-e2-mood-substances:41916924;ndem-e2-mood-substances:42286971;ndem-f-daydreaming-rejection:41789525;ndem-f-daydreaming-rejection:42147013;ndem-f-daydreaming-rejection:42341946 |

## Interpretation boundaries

- Agreement shows reproducibility between two AI-family review passes under frozen inputs; disagreement identifies adjudication workload.
- Neither reviewer is a reference standard. Concordant error and shared-corpus bias remain possible.
- Abstract-only judgments may change after full-text review and design-specific risk-of-bias assessment.
- Human Reviewer 2, clinical sign-off, and official engine export remain pending.

## Gate status

```json
{
  "secondary_ai_reviewer_claude": "complete",
  "ai_ai_reconciliation": "draft",
  "human_reviewer_2": "pending",
  "full_text_screening": "pending",
  "formal_risk_of_bias": "pending",
  "clinical_sign_off": "pending",
  "official_engine_export": "withheld"
}
```
