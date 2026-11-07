# Search strategy

> **Status:** AI Reviewer‑1 rapid-review search record. Human Reviewer 2 and information-specialist peer review are pending.

- Final search date: **2026-07-29**
- Date coverage: database inception through 2026-07-29.
- Language: no explicit database language filter. Records without an inspectable abstract could not support extraction.
- Executed sources: PubMed/MEDLINE and Europe PMC for all modules; ClinicalTrials.gov for D, E1, and E2.
- Not executed: Embase, PsycINFO, Web of Science, Scopus, Cochrane CENTRAL, CINAHL, ERIC, ProQuest, and WHO ICTRP.
- Retrieval was live and capped by source. A source cap is a rapid-review constraint, not proof that all database records were retrieved.

## Main module searches

### ndem-a-persistence-compensation

- Executed sources: pubmed, europepmc
- Counts: identified 560; within-module deduplicated 477; live verified 472; unresolved/rejected 5.
- Source caps: `{"europepmc": 300, "pubmed": 300}`

#### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR ADHD OR "attention deficit hyperactivity disorder" ) AND ( persistence OR longitudinal OR follow-up OR "executive function" OR "functional impairment" OR intelligence OR gifted OR achievement OR compensation OR masking ) AND ( "Longitudinal Studies" OR adult OR adolescent OR cohort OR prospective OR retrospective OR "Systematic Review" OR "Meta-Analysis" )
```

#### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR ADHD[tiab] OR "attention deficit hyperactivity disorder"[tiab] ) AND ( persistence[tiab] OR longitudinal[tiab] OR follow-up[tiab] OR "executive function"[tiab] OR "functional impairment"[tiab] OR intelligence[tiab] OR gifted[tiab] OR achievement[tiab] OR compensation[tiab] OR masking[tiab] ) AND ( "Longitudinal Studies"[Mesh] OR adult[tiab] OR adolescent[tiab] OR cohort[tiab] OR prospective[tiab] OR retrospective[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

### ndem-b-overlap-social-dcd

- Executed sources: pubmed, europepmc
- Counts: identified 599; within-module deduplicated 535; live verified 516; unresolved/rejected 19.
- Source caps: `{"europepmc": 300, "pubmed": 300}`

#### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Autism Spectrum Disorder" OR "Developmental Coordination Disorder" OR ADHD OR autism OR autistic OR "developmental coordination disorder" OR dyspraxia ) AND ( comorbidity OR overlap OR "differential diagnosis" OR "social communication" OR "social functioning" OR peer OR bullying OR literal OR "motor coordination" OR "adaptive functioning" ) AND ( adult OR adolescent OR cohort OR "Systematic Review" OR "Meta-Analysis" )
```

#### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Autism Spectrum Disorder"[Mesh] OR "Developmental Coordination Disorder"[Mesh] OR ADHD[tiab] OR autism[tiab] OR autistic[tiab] OR "developmental coordination disorder"[tiab] OR dyspraxia[tiab] ) AND ( comorbidity[tiab] OR overlap[tiab] OR "differential diagnosis"[tiab] OR "social communication"[tiab] OR "social functioning"[tiab] OR peer[tiab] OR bullying[tiab] OR literal[tiab] OR "motor coordination"[tiab] OR "adaptive functioning"[tiab] ) AND ( adult[tiab] OR adolescent[tiab] OR cohort[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

### ndem-c-context-motivation-strengths

- Executed sources: pubmed, europepmc
- Counts: identified 600; within-module deduplicated 564; live verified 533; unresolved/rejected 31.
- Source caps: `{"europepmc": 300, "pubmed": 300}`

#### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Autism Spectrum Disorder" OR ADHD OR autism OR autistic ) AND ( interest OR novelty OR reward OR urgency OR "immediate feedback" OR "person-environment fit" OR "job crafting" OR autonomy OR workplace OR hyperfocus OR creativity OR "divergent thinking" OR "pattern recognition" ) AND ( experiment OR performance OR function OR impairment OR cohort OR "Systematic Review" OR "Meta-Analysis" )
```

#### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Autism Spectrum Disorder"[Mesh] OR ADHD[tiab] OR autism[tiab] OR autistic[tiab] ) AND ( interest[tiab] OR novelty[tiab] OR reward[tiab] OR urgency[tiab] OR "immediate feedback"[tiab] OR "person-environment fit"[tiab] OR "job crafting"[tiab] OR autonomy[tiab] OR workplace[tiab] OR hyperfocus[tiab] OR creativity[tiab] OR "divergent thinking"[tiab] OR "pattern recognition"[tiab] ) AND ( experiment[tiab] OR performance[tiab] OR function[tiab] OR impairment[tiab] OR cohort[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

### ndem-d-external-scaffolding

- Executed sources: pubmed, europepmc, clinicaltrials
- Counts: identified 606; within-module deduplicated 548; live verified 535; unresolved/rejected 13.
- Source caps: `{"clinicaltrials": 100, "europepmc": 300, "pubmed": 300}`

#### clinicaltrials

```text
ADHD coaching organizational skills
```

#### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR ADHD OR "executive dysfunction" ) AND ( coaching OR "cognitive behavioral therapy" OR "organizational skills" OR checklist OR reminder OR "digital intervention" OR "assistive technology" OR "cognitive aid" OR "external scaffold" OR "occupational redesign" OR "educational accommodation" OR "artificial intelligence" ) AND ( randomized OR randomised OR trial OR controlled OR "Systematic Review" OR "Meta-Analysis" )
```

#### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR ADHD[tiab] OR "executive dysfunction"[tiab] ) AND ( coaching[tiab] OR "cognitive behavioral therapy"[tiab] OR "organizational skills"[tiab] OR checklist[tiab] OR reminder[tiab] OR "digital intervention"[tiab] OR "assistive technology"[tiab] OR "cognitive aid"[tiab] OR "external scaffold"[tiab] OR "occupational redesign"[tiab] OR "educational accommodation"[tiab] OR "artificial intelligence"[tiab] ) AND ( randomized[tiab] OR randomised[tiab] OR trial[tiab] OR controlled[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

### ndem-e1-sleep-circadian

- Executed sources: pubmed, europepmc, clinicaltrials
- Counts: identified 600; within-module deduplicated 537; live verified 513; unresolved/rejected 24.
- Source caps: `{"clinicaltrials": 100, "europepmc": 300, "pubmed": 300}`

#### clinicaltrials

```text
ADHD bipolar sleep circadian
```

#### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Bipolar Disorder" OR ADHD OR "bipolar disorder" OR mania OR hypomania ) AND ( "Sleep Deprivation" OR "Circadian Rhythm Sleep Disorders" OR sleep OR insomnia OR circadian OR "sleep deprivation" OR "sleep duration" OR "sleep quality" OR "shift work" OR "delayed sleep phase" ) AND ( attention OR "executive function" OR cognition OR irritability OR mood OR mania OR psychosis OR longitudinal OR prospective OR "Systematic Review" OR "Meta-Analysis" )
```

#### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Bipolar Disorder"[Mesh] OR ADHD[tiab] OR "bipolar disorder"[tiab] OR mania[tiab] OR hypomania[tiab] ) AND ( "Sleep Deprivation"[Mesh] OR "Circadian Rhythm Sleep Disorders"[Mesh] OR sleep[tiab] OR insomnia[tiab] OR circadian[tiab] OR "sleep deprivation"[tiab] OR "sleep duration"[tiab] OR "sleep quality"[tiab] OR "shift work"[tiab] OR "delayed sleep phase"[tiab] ) AND ( attention[tiab] OR "executive function"[tiab] OR cognition[tiab] OR irritability[tiab] OR mood[tiab] OR mania[tiab] OR psychosis[tiab] OR longitudinal[tiab] OR prospective[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

### ndem-e2-mood-substances

- Executed sources: pubmed, europepmc, clinicaltrials
- Counts: identified 700; within-module deduplicated 642; live verified 623; unresolved/rejected 19.
- Source caps: `{"clinicaltrials": 100, "europepmc": 350, "pubmed": 350}`

#### clinicaltrials

```text
ADHD bipolar cannabis stimulant nicotine
```

#### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Bipolar Disorder" OR "Cannabis" OR "Central Nervous System Stimulants" OR "Nicotine" OR ADHD OR "bipolar disorder" OR mania OR hypomania OR cannabis OR THC OR marijuana OR methylphenidate OR amphetamine OR stimulant OR nicotine ) AND ( psychosis OR mania OR hypomania OR "mood destabilization" OR activation OR impulsivity OR salience OR "rapid speech" OR cognition OR "executive function" OR irritability ) AND ( guideline OR longitudinal OR cohort OR trial OR "Systematic Review" OR "Meta-Analysis" )
```

#### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Bipolar Disorder"[Mesh] OR "Cannabis"[Mesh] OR "Central Nervous System Stimulants"[Mesh] OR "Nicotine"[Mesh] OR ADHD[tiab] OR "bipolar disorder"[tiab] OR mania[tiab] OR hypomania[tiab] OR cannabis[tiab] OR THC[tiab] OR marijuana[tiab] OR methylphenidate[tiab] OR amphetamine[tiab] OR stimulant[tiab] OR nicotine[tiab] ) AND ( psychosis[tiab] OR mania[tiab] OR hypomania[tiab] OR "mood destabilization"[tiab] OR activation[tiab] OR impulsivity[tiab] OR salience[tiab] OR "rapid speech"[tiab] OR cognition[tiab] OR "executive function"[tiab] OR irritability[tiab] ) AND ( guideline[tiab] OR longitudinal[tiab] OR cohort[tiab] OR trial[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

### ndem-f-daydreaming-rejection

- Executed sources: pubmed, europepmc
- Counts: identified 500; within-module deduplicated 467; live verified 446; unresolved/rejected 21.
- Source caps: `{"europepmc": 250, "pubmed": 250}`

#### europepmc

```text
( "maladaptive daydreaming" OR "immersive daydreaming" OR ADHD OR autism ) AND ( loneliness OR rejection OR bullying OR shame OR boredom OR anxiety OR trauma OR dissociation OR "rejection sensitivity" OR "emotion regulation" ) AND ( association OR longitudinal OR prospective OR mediation OR qualitative OR "Systematic Review" )
```

#### pubmed

```text
( "maladaptive daydreaming"[tiab] OR "immersive daydreaming"[tiab] OR ADHD[tiab] OR autism[tiab] ) AND ( loneliness[tiab] OR rejection[tiab] OR bullying[tiab] OR shame[tiab] OR boredom[tiab] OR anxiety[tiab] OR trauma[tiab] OR dissociation[tiab] OR "rejection sensitivity"[tiab] OR "emotion regulation"[tiab] ) AND ( association[tiab] OR longitudinal[tiab] OR prospective[tiab] OR mediation[tiab] OR qualitative[tiab] OR "Systematic Review"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Targeted PubMed gap-filling searches

### TPS-NICOTINE-INTERACTION

- Module: ndem-e2-mood-substances
- Purpose: Direct evidence for nicotine activation/attention effects and nicotine-prescription-stimulant interaction.
- Total found: 195
- Selected after abstract inspection: 37216808, 40050542

```text
(nicotine[Title] OR tobacco[Title]) AND (attention deficit hyperactivity disorder[Title/Abstract] OR ADHD[Title/Abstract]) AND (attention[Title/Abstract] OR cognition[Title/Abstract] OR symptoms[Title/Abstract])
```

### TPS-CANNABIS-HARM

- Module: ndem-e2-mood-substances
- Purpose: High-level current synthesis of psychiatric, psychosis, cognitive, mania, and mood-course outcomes.
- Total found: 219
- Selected after abstract inspection: 37648266, 38655516

```text
(cannabis OR THC) AND (psychosis OR bipolar OR mania) AND (systematic review[Publication Type] OR meta-analysis[Publication Type])
```

### TPS-RECALL-CONFIRMATION

- Module: global
- Purpose: Directly test retrospective-onset uncertainty and confirmation-bias counterclaims.
- Total found: 3
- Selected after abstract inspection: 30843223, 35609443

```text
((adult ADHD[Title/Abstract] OR adult autism[Title/Abstract]) AND (retrospective recall[Title/Abstract] OR childhood recall[Title/Abstract] OR self-report bias[Title/Abstract] OR diagnostic recall[Title/Abstract])) OR ((psychiatric diagnosis[Title/Abstract]) AND (confirmation bias[Title/Abstract] OR narrative bias[Title/Abstract]))
```

## Deduplication and screening

Records were normalized by verified identifier, preferring PMID where available. Within-module exact identifier duplicates were collapsed during grounding. Cross-module duplicates were retained because eligibility and claim mapping differ by module.

A deterministic evidence-hierarchy triage ranked verified records. Exactly 140 records per module advanced to AI Reviewer‑1 title/abstract screening. Records beyond that cap were labelled `not_advanced_due_rapid_review_cap`; they were **not** counted as exclusions.

AI Reviewer‑1 screened 980 module-record occurrences: 206 include, 27 uncertain, and 747 exclude. There were 216 unique PMIDs among include/uncertain decisions. Human Reviewer 2 has not adjudicated these decisions.

Selection did not depend on whether a result favored the model. The extraction sample prioritized guidelines, systematic reviews/meta-analyses, longitudinal cohorts, randomized trials, direct counterevidence, and coverage of every atomic claim. Abstracts were required; title-only sources were not cited.

Post-hoc eligibility deviation: PMIDs 40868584, 42199316, and 42327684 have nonseparable pediatric or mixed populations that do not satisfy the frozen target-population criteria. They are retained only as explicitly labelled hypothesis-generating context, have neutral direction in the matrix, and do not contribute to claim verdicts.

## Citation chaining

Method: PubMed ELink Similar Articles through the PubMed plugin. Gap detection only. Returned titles were not cited unless they independently entered the verified extraction set and their abstract was inspected.

- Seed PMID 37648266 (Cannabis risks and benefits umbrella review): 20 related records returned. No new source added. The seed umbrella review already synthesized the relevant psychiatric meta-analyses; unrelated treatment/pain results were not advanced.
- Seed PMID 42365682 (Childhood ADHD to adult functional outcomes): 20 related records returned. No new primary source added solely from the related-article list. The late-onset/persistence uncertainty was separately covered by an inspected critical review (PMID 30843223), and PMID 40287009 was already in the screened evidence set.

Reference and cited-by ELink calls for PMID 37648266 returned no records; this connector limitation is logged rather than treated as absence of citations.

## PRISMA-style rapid-review flow

| Module | Identified | Deduplicated | Live verified | Advanced | Deferred (not screened) | Included | Uncertain | Excluded | Extracted |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 560 | 477 | 472 | 140 | 332 | 34 | 2 | 104 | 14 |
| B | 599 | 535 | 516 | 140 | 376 | 37 | 2 | 101 | 14 |
| C | 600 | 564 | 533 | 140 | 393 | 12 | 2 | 126 | 14 |
| D | 606 | 548 | 535 | 140 | 395 | 26 | 8 | 106 | 14 |
| E1 | 600 | 537 | 513 | 140 | 373 | 35 | 8 | 97 | 14 |
| E2 | 700 | 642 | 623 | 140 | 483 | 44 | 3 | 93 | 14 |
| F | 500 | 467 | 446 | 140 | 306 | 18 | 2 | 120 | 12 |

Totals are module-record occurrences because the protocol contains seven distinct eligibility frameworks. Across modules, 4,165 records were identified, 3,770 remained after within-module deduplication, 3,638 were live verified, 132 were unresolved/rejected, 980 were screened, and 2,658 were deferred by the rapid cap. Extraction reconciles as 96 module occurrences plus 2 global targeted supplements = 98 occurrences (92 unique PMIDs). No formal full-text eligibility stage has yet been completed.

## Reproducibility files

- Main run manifests: `/Users/neurologic/Projects/evidence-synthesis-engine-neurodevelopmental-review/runs/ndem-rapid-20260729`
- Frozen claim ledger: `/Users/neurologic/Projects/evidence-synthesis-engine-neurodevelopmental-review/review_inputs/neurodevelopmental-environmental-mismatch/claim_ledger.csv`
- Protocol specifications: `/Users/neurologic/Projects/evidence-synthesis-engine-neurodevelopmental-review/review_inputs/neurodevelopmental-environmental-mismatch`
- Bibliographic verification XML: `/Users/neurologic/Projects/evidence-synthesis-engine-neurodevelopmental-review/runs/ndem-rapid-20260729/selected_pubmed_efetch.xml`
