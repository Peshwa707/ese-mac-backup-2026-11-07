# Cochrane-Standard SR/MA Workflow — Engine Spec & Gap Map

**Source:** Cochrane Handbook for Systematic Reviews of Interventions, **v6.5 (2024)**, fetched from
training.cochrane.org (redirects to cochrane.org/authors/handbooks-and-manuals/handbook/current) on 2026-07-09.
Chapters are cited inline as `[Ch N §S.S]`. This document **distills and cites** the Handbook — it does not
reproduce it.

**Purpose.** This is the methodological target the engine is measured against. Each engine stage is mapped to the
Cochrane requirement(s) it must satisfy, with an honest **engine-does / Cochrane-requires / gap** triad and a
severity. §"Punch-list" at the end is the prioritized backlog — "all the other fixes" — derived from the red rows.

**How to read severity.** 🔴 Critical = produces or enables an invalid/misleading review; 🟠 High = a Cochrane/MECIR
mandate the engine cannot currently meet; 🟡 Medium = partial compliance or missing rigor; 🟢 Done = already
compliant (credited so we don't regress it).

**Honesty note.** "Engine does" reflects behavior verified this session (code read + the antithrombin run
`db6f8f7426e0`), not aspiration. Where engine behavior was not directly verified it is marked *(verify)*.

---

## 0. Cross-cutting requirements (apply to the whole pipeline)

| # | Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|---|
| X1 | **Dual independent reviewers** at screening `[Ch 4 §4.6.3]`, full-text selection `[Ch 4 §4.6.3]`, data extraction `[Ch 5 §5.5.2]`, and RoB `[Ch 8]`, with a pre-specified disagreement/adjudication step and κ. | AI = Reviewer 1; a single human signs an honesty gate (Reviewer 2) at screen + extract. No independent second *assessment*, no κ, no recorded disagreement set. | The human gate is an *approval*, not an independent second pass. Need a true R2 (second model or human) + reconciliation/κ + adjudication log. Disclose as "rapid review" if only one independent pass. | 🔴 |
| X2 | **Unit of assessment is (study × outcome × result)**, not the study — RoB, GRADE, and effect selection are all per-outcome `[Ch 7 §7.1.2]` `[Ch 8 §8.2.4]` `[Ch 14 §14.2.1]`. | GRADE is per-outcome 🟢. RoB config is keyed per-study by design; extraction picks one estimate per field. | RoB and result-selection must key on (study × outcome), with a pre-specified multiplicity hierarchy `[Ch 3 §3.2.4.3]`. | 🟠 |
| X3 | **Pre-registered protocol** (PROSPERO) with locked eligibility, outcomes, and analysis before screening; log every post-hoc deviation `[Ch 1]` `[Ch 3 §3.1]`. | Vertical config (pico/search/extraction/grade) is authored up front and is now pinned to the run. No registration artifact, no protocol-vs-final deviation log. | Emit a protocol artifact + a deviation audit trail. | 🟠 |
| X4 | **PRISMA 2020 reporting** + PRISMA-S for the search; counts reconcile end-to-end `[Ch 4 §4.4.5]`. | Produces identified→prescreen→screen→include counts + a manuscript export. | Verify full PRISMA 2020 + PRISMA-S flow diagram with per-node reconciliation and per-exclusion reasons. | 🟡 |
| X5 | **Study (not report) is the unit**; link multiple reports of one trial `[Ch 5 §5.2.1]` `[Ch 4 §4.2.3]`. | De-dups records; verifies PMIDs. Registry/multi-report linking *(verify)*. | Ensure study-level linking (registration IDs, author/sponsor/N/dates), not just record de-dup, to avoid double-counting. | 🟠 |

---

## 1. Stage-by-stage map (engine's 11 stages)

### `search` — Ch 4 (searching), Ch 13 §13.2 (missing results)
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| CENTRAL + MEDLINE + Embase are the mandatory core `[Ch 4 §4.3.1.3 / MECIR C24]`; add topic databases `[C25]`. | PubMed executed live (274 records). Europe PMC + ClinicalTrials.gov configured; other DBs = strings-only, `executed:false`. In the antithrombin run only PubMed returned records. | Not searching CENTRAL/Embase live = non-compliant core coverage. Wire real multi-database execution (or import), not strings-only. | 🔴 |
| Search **trial registries** (ClinicalTrials.gov **and** WHO ICTRP) + regulatory sources `[Ch 4 §4.3.3]` `[Ch 13 §13.2.1]`. | ClinicalTrials.gov configured; WHO ICTRP absent; no regulatory (FDA/EMA) source. | Add ICTRP + regulatory retrieval; these counter publication bias. | 🟠 |
| **Grey literature + reference-list + forward-citation** searching `[Ch 4 §4.3.5]`. | None automated (reference-mining seeds field exists, empty). | Add citation/reference chasing + grey-lit surface. | 🟠 |
| Search designed with an **information specialist** and **peer-reviewed (PRESS)** before running `[Ch 4 §4.2.1–4.2.2]`. | Query authored in YAML; no PRESS gate. | Add a pre-run search-QA/PRESS checklist gate. | 🟡 |
| Full strategy documented to **PRISMA-S**, reproducible `[Ch 4 §4.5]`. | search_manifest.json records queries + per-source counts. | Confirm PRISMA-S completeness (limits, dates, exact strings per source). | 🟢/🟡 |

### `prescreen` — Ch 3 §3.2.4.1, Ch 4 §4.6
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| **Do not exclude on outcome reporting** `[Ch 3 §3.2.4.1]`; exclude only by design/publication-type at this stage. | Rule-based exclusion of reviews/editorials/etc. by publication type (44 excluded in the run). | Confirm no outcome-based auto-exclusion leaks in here. | 🟢 |

### `screen` — Ch 4 §4.6.3–4.6.5
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| **Two independent screeners**, adjudicated disagreements, κ `[Ch 4 §4.6.3–4.6.4]`. | Single AI screener (include/exclude/uncertain) → human approves the gate. | Second independent screen + κ + adjudication log (see X1). | 🔴 |
| Record **reason per full-text exclusion** in a Characteristics-of-excluded-studies table `[Ch 4 §4.6.5]`. | Exclusion buckets exist; per-study reason capture at full text *(verify)*. | Ensure every full-text exclusion carries a specific, persisted reason. | 🟡 |

### `fulltext` — Ch 5 §5.2
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| Extract from **full text, not abstracts** `[Ch 5 §5.2]`. | Retrieves full text where machine-available (1/4 via PMC in the run); 3/4 abstract-only. | Full-text retrieval coverage is the binding constraint — most numeric data lives in full-text tables. Improve retrieval (publisher/PMC/interlibrary) or require human upload. | 🔴 |

### `extract` — Ch 5 (collecting), Ch 6 (effect measures)
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| **Two independent extractors** + reconciliation `[Ch 5 §5.5.2]`. | Single AI extraction, cite-or-omit, NR-never-fabricated (honest 🟢). | Second independent extraction + reconciliation (see X1). | 🟠 |
| Record **exact raw data**: per-arm N, events+denominators (2×2), means+SDs+timepoints, CIs/p `[Ch 5 §5.6]`. | Schema captures per-arm n/mean/sd + events; abstract-level → mostly NR in the run. | Gap is full-text-driven (see fulltext 🔴). Schema itself is adequate. | 🟡 |
| Correct **effect-measure handling**: MD when same scale, SMD (Hedges) when different + direction-harmonized `[Ch 6 §6.5.1]`; lock event polarity `[Ch 6 §6.4.1.5]`; HR path for time-to-event `[Ch 6 §6.8.1]`; guard SD derivation, no range→SD `[Ch 6 §6.5.2]`. | Extraction schema declares pool type (continuous/proportion/binary_2x2) + beneficial direction. Stats applies Hedges' g. | Add explicit scale-direction harmonization + SD-derivation provenance/validation + skew flag `[Ch 10 §10.5.3]`; dedicated HR/time-to-event path *(verify)*. | 🟠 |
| **Contact authors** for missing data `[Ch 5 §5.2.3]`. | None. | Flag "data absent" as an author-query action, don't silently NR-drop. | 🟡 |

### `rob` — Ch 7 (bias/COI), Ch 8 (RoB 2)
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| **RoB 2 actually performed**, all 5 domains, signalling questions + evidence stored, algorithmic domain→overall mapping, **per result**, two independent assessors `[Ch 8 §8.2.3–8.2.4]`. | RoB stage present but **not run** in the antithrombin pipeline (n=0, "run harness agents or re-stage"). Config = RoB2 by design, keyed per-study. | Make RoB run by default; assess per (study×outcome); store signalling-question answers + quotes; two independent assessors + reconciliation. | 🔴 |
| **COI/funding** captured and fed into the selective-reporting domain `[Ch 7 §7.8.3–7.8.5]`. | Not captured. | Extract funding/COI from report/registry/CSR; route into RoB. | 🟠 |

### `stats` — Ch 9 (prepare), Ch 10 (MA), Ch 11 (NMA), Ch 12 (other)
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| **Pre-synthesis homogeneity gate** — decide what may be combined; sometimes don't pool `[Ch 9 §9.3.2]` `[Ch 12 §12.1]`. | Pools whatever poolable fields exist; in the run, nothing poolable → correctly produced nothing. | Add an explicit "similar enough to combine?" gate + a documented **non-MA / SWiM** branch `[Ch 12 §12.2]` (never significance vote-counting). | 🟠 |
| Random-effects with **REML/PM** τ² + **Hartung-Knapp** CI + **prediction interval** (≥3–5 studies) `[Ch 10 §10.10.4.4–5]`; **Mantel-Haenszel**/Peto for sparse/rare dichotomous `[Ch 10 §10.4.1–4.4]`; MD vs SMD by scale `[Ch 10 §10.5.1]`; skew flag `[Ch 10 §10.5.3]`. | `metacont`/`metabin`: `common=FALSE, random=TRUE, method.random.ci="HK"`, `method.smd="Hedges"`, `method="MH"`, `prediction = n≥3`. | **Core MA mechanics are Cochrane-aligned** 🟢. Add: Peto path for rare events; skew flag; surface τ²+CI/I²/Q completely `[Ch 10 §10.10.2]`. | 🟢/🟡 |
| **Subgroups pre-specified + formal interaction test**; never infer from separate pools `[Ch 10 §10.11.2–3]`. | Subgroup by `subgroup_field` (e.g. formulation), stratified pass. | Add a formal between-subgroup interaction test; treat post-hoc/few-study subgroups as hypothesis-generating. | 🟠 |
| **Unit-of-analysis** handling (cluster/crossover/multi-arm via generic IV; split shared control) `[Ch 10 §10.3.3]`. | *(verify)* — not observed. | Add cluster/crossover/multi-arm handling to avoid double-counting. | 🟠 |
| **NMA transitivity + connected network + node-splitting + global inconsistency + ranking-with-uncertainty (SUCRA/rankogram, never bare winner)** `[Ch 11 §11.2.2, §11.4.4, §11.4.3.3]`. | `nma.R`: gemtc Bayesian random-effects **consistency** model only. | No transitivity check, no node-split/global inconsistency test, no SUCRA/rankogram/league-with-CI surfaced. Add these before any NMA is trusted. | 🔴 |

### `evidence_strength` / `grade` — Ch 13 (reporting bias), Ch 14 (GRADE/SoF)
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| **Publication-bias assessment**: registry hunt, funnel/asymmetry **only ≥10 studies** and test matched to measure (Harbord/Peters for OR; not Egger for OR/SMD), asymmetry ≠ verdict; formal ROB-ME into GRADE `[Ch 13 §13.3.4.4–5, §13.3.5]`. | `evidence_strength` flags suppressed/non-comparative/surrogate/indirect. GRADE defers pub-bias + inconsistency to "Phase B." | Implement the ≥10-study-gated, measure-matched asymmetry path + ROB-ME judgment feeding GRADE. | 🟠 |
| **GRADE per outcome**, start-by-design, 5 downgrade + 3 upgrade domains, **not mechanical** (inconsistency only if unexplained; imprecision via **OIS**, not "CI crosses null"), footnoted rationale `[Ch 14 §14.2.1–3]`. | Deterministic per-outcome GRADE 🟢; start High for RCT 🟢; downgrade rules for indirectness/RoB-fraction/imprecision(min_studies). No upgrade domains; imprecision = study-count, not OIS; inconsistency/pub-bias deferred. | Add upgrade domains, OIS-based imprecision, unexplained-heterogeneity check for inconsistency, per-domain footnotes. | 🟠 |
| **SoF table** with baseline risk + **absolute effect (95% CI)** + relative effect + N studies/participants + certainty `[Ch 14 §14.1.6.3–8]`. | Exports a manuscript; absolute-effect/SoF completeness *(verify)*. | Ensure a compliant SoF with **mandatory absolute effects/NNT** from an assumed comparator risk. | 🟠 |

### `assemble` / `manuscript` — Ch 3 §III (reporting), Ch 15 (interpretation)
| Cochrane requires | Engine does | Gap | Sev |
|---|---|---|---|
| Report **both relative and absolute** effects + NNT `[Ch 15 §15.4]`. | Manuscript export exists. | Add absolute effects/NNT to output. | 🟠 |
| **Conclusion wording tracks GRADE certainty** (High "reduces" → Very Low "very uncertain"); never "no evidence of effect" = "evidence of no effect"; no "statistically significant" — report CI+exact P `[Ch 15 §15.6.4, Table 15.6.b, §15.3.2]`. | Free-form manuscript prose (the antithrombin draft was hand-written by the DA, honesty-hedged). | Template narrative statements by certainty level; add the "blinded direction-reversal" spin check. | 🟠 |
| Interpret continuous effects vs **MID**; separate Implications for Practice vs Research mapped to the limiting GRADE domain `[Ch 15 §15.5, §15.6.2–3]`. | None. | Add MID references per instrument + structured research-gap output. | 🟡 |

---

## 2. Punch-list — "all the other fixes", prioritized

**🔴 Critical (invalidity risk — do first)**
1. **Multi-database live search** (CENTRAL + MEDLINE/PubMed + Embase core; +ICTRP) — `search` — `[Ch 4 §4.3.1.3]`. Single-source search is non-compliant and biased.
2. **Dual independent screening + extraction** (second model/human + reconciliation + κ + adjudication log); until then, label output a **rapid review**, not a full SR — `screen`/`extract` — `[Ch 4 §4.6.3]` `[Ch 5 §5.5.2]`.
3. **RoB 2 runs by default**, per (study×outcome), with signalling-question evidence + two assessors — `rob` — `[Ch 8]`. Currently skipped entirely.
4. **Full-text retrieval coverage** — most numeric data is unreachable at abstract level — `fulltext` — `[Ch 5 §5.2]`.
5. **NMA validity guards** (transitivity check, connectivity, node-splitting + global inconsistency, ranking-with-uncertainty) before any network estimate — `stats/nma` — `[Ch 11 §11.2.2, §11.4.4]`.

**🟠 High (mandate gaps)**
6. Trial-registry (ICTRP) + regulatory + grey-lit + reference/citation searching — `search` — `[Ch 4 §4.3.3–4.3.5]` `[Ch 13 §13.2.1]`.
7. Study-level (not record-level) linking of multiple reports — cross-cutting — `[Ch 5 §5.2.1]`.
8. Effect-measure hardening: scale-direction harmonization, SD-derivation provenance + skew flag, dedicated HR path, Peto for rare events — `extract`/`stats` — `[Ch 6]` `[Ch 10 §10.4.4]`.
9. Formal subgroup interaction test + unit-of-analysis handling (cluster/crossover/multi-arm) — `stats` — `[Ch 10 §10.11, §10.3.3]`.
10. Publication-bias module (≥10-study gate, measure-matched test, ROB-ME → GRADE) — `evidence_strength`/`grade` — `[Ch 13]`.
11. GRADE upgrade domains + OIS-based imprecision + unexplained-heterogeneity inconsistency + footnoted rationale — `grade` — `[Ch 14 §14.2]`.
12. SoF table with mandatory **absolute effects/NNT**; certainty-tracked conclusion wording; no "statistically significant" — `manuscript` — `[Ch 14 §14.1.6]` `[Ch 15]`.
13. COI/funding capture → selective-reporting domain — `rob` — `[Ch 7 §7.8]`.
14. Protocol/PROSPERO artifact + protocol-vs-final deviation log — cross-cutting — `[Ch 1]` `[Ch 3 §3.1]`.

**🟡 Medium (rigor/completeness)**
15. PRESS search-strategy QA gate — `search` — `[Ch 4 §4.2.2]`.
16. Pre-synthesis homogeneity gate + explicit SWiM non-MA branch (never significance vote-counting) — `stats` — `[Ch 9 §9.3.2]` `[Ch 12]`.
17. Full PRISMA 2020 + PRISMA-S flow with per-exclusion reasons reconciled — cross-cutting — `[Ch 4 §4.4.5]`.
18. Author-contact flag for missing data; MID references per instrument; structured research-gap output — `extract`/`manuscript` — `[Ch 5 §5.2.3]` `[Ch 15]`.

---

## 3. What the engine already gets right (don't regress)

- **Meta-analysis core is Cochrane-aligned**: random-effects, Hartung-Knapp CIs, Hedges' g SMD, Mantel-Haenszel dichotomous, prediction intervals at ≥3 `[Ch 10 §10.5, §10.10.4]`.
- **GRADE is per-outcome, deterministic, starts High for RCTs** `[Ch 14 §14.2.1]`.
- **Honesty contract**: cite-or-omit, NR-never-fabricated, AI = Reviewer 1, fail-closed export gate — aligns with the transparency spirit of `[Ch 4 §4.5]` `[Ch 5 §5.6]` and directly supports MECIR auditability.
- **Vertical now pinned to the run** (this session's fix) — a prerequisite for any reproducible protocol artifact (X3).

---

## 4. Chapters cited (Cochrane Handbook v6.5, 2024)

Ch 1 Starting a review · Ch 2 Scope & questions · Ch 3 Inclusion criteria & grouping · Ch 4 Searching & selecting ·
Ch 5 Collecting data · Ch 6 Effect measures · Ch 7 Bias & COI · Ch 8 RoB 2 · Ch 9 Preparing for synthesis ·
Ch 10 Meta-analyses · Ch 11 Network meta-analyses · Ch 12 Synthesis using other methods · Ch 13 Missing results /
reporting bias · Ch 14 SoF & GRADE · Ch 15 Interpreting results. Base URL:
`cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-NN`.

*Section numbers were transcribed from the chapter text fetched this session; spot-verify a citation against the
live chapter before hard-coding it into a stage's docstring.*
