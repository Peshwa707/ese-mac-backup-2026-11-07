# Search strategy and Claude secondary-review methods

> Claude served as a blinded secondary AI reviewer under the same frozen rapid-review protocol. This is an AI-AI comparison input, not Human Reviewer 2 screening, full-text adjudication, formal risk-of-bias/GRADE completion, treating-clinician sign-off, diagnosis, treatment guidance, or authorization to unlock conclusions. All disagreements remain pending human adjudication.

Claude did not rerun database searches. To make reviewer agreement interpretable, it received the exact frozen ordered title/abstract corpus produced by the evidence-synthesis engine for Codex Reviewer 1. Search execution, queries, caps, and frozen-set hashes are reproduced below.

- Sources actually executed: PubMed and Europe PMC.
- Publication cutoff: 2026-07-29.
- Profile: rapid systematic evidence review.
- Per-module rapid cap: 140 verified title/abstract records.
- Deduplication: within-module identifier/title normalization in the engine; cross-module occurrences retained.
- Screening: Claude Opus 5, fresh safe-mode no-tool sessions, one ordered 140-record batch per module.
- Full text: not completed; all included/uncertain records remain full-text required.
- Citation chaining and targeted Reviewer-1 supplements: not supplied to Claude because the stored files reveal Reviewer-1 selections; excluded from primary independence metrics.

## Module A: Developmental persistence, executive function, and compensation

- Search started: `2026-07-30T04:06:46Z`
- Search completed: `2026-07-30T04:06:59Z`
- Identified: 560; deduped: 477; verified: 472; rejected: 5.
- Claude screening: include 29, uncertain 8, exclude 103.
- Frozen set hash: `dac27b86ce9040d349a03185cefa86aca661a5b1ad769cbc650dbac0fb2fc9a7`

### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR ADHD OR "attention deficit hyperactivity disorder" ) AND ( persistence OR longitudinal OR follow-up OR "executive function" OR "functional impairment" OR intelligence OR gifted OR achievement OR compensation OR masking ) AND ( "Longitudinal Studies" OR adult OR adolescent OR cohort OR prospective OR retrospective OR "Systematic Review" OR "Meta-Analysis" )
```

### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR ADHD[tiab] OR "attention deficit hyperactivity disorder"[tiab] ) AND ( persistence[tiab] OR longitudinal[tiab] OR follow-up[tiab] OR "executive function"[tiab] OR "functional impairment"[tiab] OR intelligence[tiab] OR gifted[tiab] OR achievement[tiab] OR compensation[tiab] OR masking[tiab] ) AND ( "Longitudinal Studies"[Mesh] OR adult[tiab] OR adolescent[tiab] OR cohort[tiab] OR prospective[tiab] OR retrospective[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Module B: ADHD-autism-DCD overlap and social functioning

- Search started: `2026-07-30T04:06:59Z`
- Search completed: `2026-07-30T04:07:15Z`
- Identified: 599; deduped: 535; verified: 516; rejected: 19.
- Claude screening: include 39, uncertain 12, exclude 89.
- Frozen set hash: `b1fcf620b45808f142112c315068c03075cf7ba0fe5bdb97753f062e6e5286e3`

### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Autism Spectrum Disorder" OR "Developmental Coordination Disorder" OR ADHD OR autism OR autistic OR "developmental coordination disorder" OR dyspraxia ) AND ( comorbidity OR overlap OR "differential diagnosis" OR "social communication" OR "social functioning" OR peer OR bullying OR literal OR "motor coordination" OR "adaptive functioning" ) AND ( adult OR adolescent OR cohort OR "Systematic Review" OR "Meta-Analysis" )
```

### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Autism Spectrum Disorder"[Mesh] OR "Developmental Coordination Disorder"[Mesh] OR ADHD[tiab] OR autism[tiab] OR autistic[tiab] OR "developmental coordination disorder"[tiab] OR dyspraxia[tiab] ) AND ( comorbidity[tiab] OR overlap[tiab] OR "differential diagnosis"[tiab] OR "social communication"[tiab] OR "social functioning"[tiab] OR peer[tiab] OR bullying[tiab] OR literal[tiab] OR "motor coordination"[tiab] OR "adaptive functioning"[tiab] ) AND ( adult[tiab] OR adolescent[tiab] OR cohort[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Module C: Context, motivation, environmental fit, and proposed strengths

- Search started: `2026-07-30T04:07:15Z`
- Search completed: `2026-07-30T04:07:27Z`
- Identified: 600; deduped: 564; verified: 533; rejected: 31.
- Claude screening: include 11, uncertain 10, exclude 119.
- Frozen set hash: `03ec4484d3df9b1d1f987f7346a6ab0d785bc682f443c6546614e31901ab9814`

### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Autism Spectrum Disorder" OR ADHD OR autism OR autistic ) AND ( interest OR novelty OR reward OR urgency OR "immediate feedback" OR "person-environment fit" OR "job crafting" OR autonomy OR workplace OR hyperfocus OR creativity OR "divergent thinking" OR "pattern recognition" ) AND ( experiment OR performance OR function OR impairment OR cohort OR "Systematic Review" OR "Meta-Analysis" )
```

### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Autism Spectrum Disorder"[Mesh] OR ADHD[tiab] OR autism[tiab] OR autistic[tiab] ) AND ( interest[tiab] OR novelty[tiab] OR reward[tiab] OR urgency[tiab] OR "immediate feedback"[tiab] OR "person-environment fit"[tiab] OR "job crafting"[tiab] OR autonomy[tiab] OR workplace[tiab] OR hyperfocus[tiab] OR creativity[tiab] OR "divergent thinking"[tiab] OR "pattern recognition"[tiab] ) AND ( experiment[tiab] OR performance[tiab] OR function[tiab] OR impairment[tiab] OR cohort[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Module D: External cognitive scaffolding

- Search started: `2026-07-30T04:07:27Z`
- Search completed: `2026-07-30T04:07:40Z`
- Identified: 606; deduped: 548; verified: 535; rejected: 13.
- Claude screening: include 33, uncertain 22, exclude 85.
- Frozen set hash: `06c55e83d496cf375c2ee218f205ae90f8f3d24badf703d9dcf4515562d23b92`

### clinicaltrials

```text
ADHD coaching organizational skills
```

### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR ADHD OR "executive dysfunction" ) AND ( coaching OR "cognitive behavioral therapy" OR "organizational skills" OR checklist OR reminder OR "digital intervention" OR "assistive technology" OR "cognitive aid" OR "external scaffold" OR "occupational redesign" OR "educational accommodation" OR "artificial intelligence" ) AND ( randomized OR randomised OR trial OR controlled OR "Systematic Review" OR "Meta-Analysis" )
```

### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR ADHD[tiab] OR "executive dysfunction"[tiab] ) AND ( coaching[tiab] OR "cognitive behavioral therapy"[tiab] OR "organizational skills"[tiab] OR checklist[tiab] OR reminder[tiab] OR "digital intervention"[tiab] OR "assistive technology"[tiab] OR "cognitive aid"[tiab] OR "external scaffold"[tiab] OR "occupational redesign"[tiab] OR "educational accommodation"[tiab] OR "artificial intelligence"[tiab] ) AND ( randomized[tiab] OR randomised[tiab] OR trial[tiab] OR controlled[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Module E1: Sleep and circadian modifiers

- Search started: `2026-07-30T04:07:40Z`
- Search completed: `2026-07-30T04:07:52Z`
- Identified: 600; deduped: 537; verified: 513; rejected: 24.
- Claude screening: include 27, uncertain 34, exclude 79.
- Frozen set hash: `dfef1d1c1cc2795657eaddf49a0e121de952c410ea81085868fd12d0147ca05a`

### clinicaltrials

```text
ADHD bipolar sleep circadian
```

### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Bipolar Disorder" OR ADHD OR "bipolar disorder" OR mania OR hypomania ) AND ( "Sleep Deprivation" OR "Circadian Rhythm Sleep Disorders" OR sleep OR insomnia OR circadian OR "sleep deprivation" OR "sleep duration" OR "sleep quality" OR "shift work" OR "delayed sleep phase" ) AND ( attention OR "executive function" OR cognition OR irritability OR mood OR mania OR psychosis OR longitudinal OR prospective OR "Systematic Review" OR "Meta-Analysis" )
```

### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Bipolar Disorder"[Mesh] OR ADHD[tiab] OR "bipolar disorder"[tiab] OR mania[tiab] OR hypomania[tiab] ) AND ( "Sleep Deprivation"[Mesh] OR "Circadian Rhythm Sleep Disorders"[Mesh] OR sleep[tiab] OR insomnia[tiab] OR circadian[tiab] OR "sleep deprivation"[tiab] OR "sleep duration"[tiab] OR "sleep quality"[tiab] OR "shift work"[tiab] OR "delayed sleep phase"[tiab] ) AND ( attention[tiab] OR "executive function"[tiab] OR cognition[tiab] OR irritability[tiab] OR mood[tiab] OR mania[tiab] OR psychosis[tiab] OR longitudinal[tiab] OR prospective[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Module E2: Mood, substances, and medication state effects

- Search started: `2026-07-30T04:07:52Z`
- Search completed: `2026-07-30T04:08:05Z`
- Identified: 700; deduped: 642; verified: 623; rejected: 19.
- Claude screening: include 45, uncertain 21, exclude 74.
- Frozen set hash: `15967d21db964b2c5a1eb73d6cb89a4248ba6fdc091f8b1878c6a0958ae7fcba`

### clinicaltrials

```text
ADHD bipolar cannabis stimulant nicotine
```

### europepmc

```text
( "Attention Deficit Disorder with Hyperactivity" OR "Bipolar Disorder" OR "Cannabis" OR "Central Nervous System Stimulants" OR "Nicotine" OR ADHD OR "bipolar disorder" OR mania OR hypomania OR cannabis OR THC OR marijuana OR methylphenidate OR amphetamine OR stimulant OR nicotine ) AND ( psychosis OR mania OR hypomania OR "mood destabilization" OR activation OR impulsivity OR salience OR "rapid speech" OR cognition OR "executive function" OR irritability ) AND ( guideline OR longitudinal OR cohort OR trial OR "Systematic Review" OR "Meta-Analysis" )
```

### pubmed

```text
( "Attention Deficit Disorder with Hyperactivity"[Mesh] OR "Bipolar Disorder"[Mesh] OR "Cannabis"[Mesh] OR "Central Nervous System Stimulants"[Mesh] OR "Nicotine"[Mesh] OR ADHD[tiab] OR "bipolar disorder"[tiab] OR mania[tiab] OR hypomania[tiab] OR cannabis[tiab] OR THC[tiab] OR marijuana[tiab] OR methylphenidate[tiab] OR amphetamine[tiab] OR stimulant[tiab] OR nicotine[tiab] ) AND ( psychosis[tiab] OR mania[tiab] OR hypomania[tiab] OR "mood destabilization"[tiab] OR activation[tiab] OR impulsivity[tiab] OR salience[tiab] OR "rapid speech"[tiab] OR cognition[tiab] OR "executive function"[tiab] OR irritability[tiab] ) AND ( guideline[tiab] OR longitudinal[tiab] OR cohort[tiab] OR trial[tiab] OR "Systematic Review"[pt] OR "Meta-Analysis"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```

## Module F: Daydreaming, rejection, anxiety, and emotional compensation

- Search started: `2026-07-30T04:08:05Z`
- Search completed: `2026-07-30T04:08:16Z`
- Identified: 500; deduped: 467; verified: 446; rejected: 21.
- Claude screening: include 44, uncertain 11, exclude 85.
- Frozen set hash: `990e06b52dcde5903d7d0bace0d5628142cda1ec80e1087ef1a6ff2d202d2737`

### europepmc

```text
( "maladaptive daydreaming" OR "immersive daydreaming" OR ADHD OR autism ) AND ( loneliness OR rejection OR bullying OR shame OR boredom OR anxiety OR trauma OR dissociation OR "rejection sensitivity" OR "emotion regulation" ) AND ( association OR longitudinal OR prospective OR mediation OR qualitative OR "Systematic Review" )
```

### pubmed

```text
( "maladaptive daydreaming"[tiab] OR "immersive daydreaming"[tiab] OR ADHD[tiab] OR autism[tiab] ) AND ( loneliness[tiab] OR rejection[tiab] OR bullying[tiab] OR shame[tiab] OR boredom[tiab] OR anxiety[tiab] OR trauma[tiab] OR dissociation[tiab] OR "rejection sensitivity"[tiab] OR "emotion regulation"[tiab] ) AND ( association[tiab] OR longitudinal[tiab] OR prospective[tiab] OR mediation[tiab] OR qualitative[tiab] OR "Systematic Review"[pt] ) AND ("1800/01/01"[dp] : "2026/07/29"[dp])
```
