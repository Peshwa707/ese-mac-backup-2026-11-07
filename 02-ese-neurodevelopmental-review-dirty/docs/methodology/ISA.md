---
task: Ingest the Cochrane Handbook into a workflow spec + gap map that directs the engine's SR/MA
project: evidence-synthesis-engine
effort: E5
phase: complete
progress: 8/8
mode: algorithm
started: 2026-07-09
updated: 2026-07-09
---

## Problem

The engine runs an SR/MA pipeline (search → prescreen → screen → fulltext → extract → rob →
stats → grade → evidence_strength → assemble → manuscript) but there is no explicit,
sourced methodological standard it is measured against. The antithrombin run exposed real
gaps (RoB never auto-run, abstract-only extraction, no quantitative synthesis, single
database executed) with no reference that says what "Cochrane-standard" requires at each
step. We need the gold-standard methodology ingested as the target spec so "all the other
fixes" become a concrete, prioritized punch-list rather than ad-hoc.

## Vision

A single repo document that, for every step of a Cochrane-standard systematic review +
meta-analysis, states (a) what the Cochrane Handbook requires, with the exact chapter
citation, (b) which engine stage owns it, and (c) the gap between the two — so Osama can
read one table and see exactly what to build next. Euphoric surprise: the messy "what
should a rigorous MA do" question collapses into a checkable, cited map where each red row
is a named fix.

## Out of Scope

- No verbatim mirroring of the copyrighted handbook — distill + cite chapters only.
- Not implementing the fixes in this run — this run produces the SPEC + gap map that scopes them.
- Not covering Cochrane-review administrative process (author teams, editorial, RevMan submission) beyond what touches the engine's methodological pipeline.

## Principles

- Confidence-requires-source: every "Cochrane requires X" claim cites a chapter fetched this session; chapter numbers/requirements are never asserted from memory alone.
- Honesty: engine "does" column reflects verified current behavior (from this session's code reading + runs), not aspiration.
- Gap map is the deliverable's spine — a spec without an honest gap column is just a summary.

## Constraints

- Source = training.cochrane.org/handbook/current (fetched + cited); respect copyright (distill, don't reproduce).
- Deliverable lives in the engine repo under docs/methodology/.
- Map onto the engine's actual 11 stages, not an idealized pipeline.

## Goal

Produce docs/methodology/cochrane-workflow.md: a per-step table mapping the Cochrane
Handbook SR/MA methodology (search, eligibility, data collection, effect measures, RoB,
synthesis prep, meta-analysis, NMA, reporting bias, GRADE, interpretation/reporting) to the
engine's stages, each row citing the handbook chapter and stating engine-does / Cochrane-
requires / gap — the sourced punch-list for the remaining fixes.

## Criteria

- [ ] ISC-1: Handbook TOC fetched; real chapter numbers/titles/URLs captured (not from memory).
- [ ] ISC-2: Key methodological chapters fetched + distilled (search/selection, data collection, effect measures, RoB, synthesis prep, meta-analysis, NMA, reporting bias, GRADE, interpretation).
- [ ] ISC-3: Every engine stage (11) mapped to ≥1 Cochrane chapter with citation.
- [ ] ISC-4: Each mapped row has an honest engine-does / Cochrane-requires / gap triad.
- [ ] ISC-5: Gaps are prioritized (severity) into a punch-list that scopes "all the other fixes."
- [ ] ISC-6: Doc written to docs/methodology/cochrane-workflow.md in the engine repo.
- [ ] ISC-7: Anti: no verbatim handbook passages reproduced; claims are distilled + cited.
- [ ] ISC-8: Anti: no "Cochrane requires" claim without a chapter citation fetched this session.

## Test Strategy

- isc | type | check | tool
- ISC-1,2 | web | fetched chapter content captured | WebFetch
- ISC-3,4,5 | doc | grep the doc for each stage + gap column populated | Read/Grep
- ISC-6 | file | file exists at path | Read
- ISC-7,8 | review | scan for uncited claims / verbatim blocks | Read

## Features

- name | satisfies | depends_on | parallelizable
- fetch-toc | ISC-1 | — | no
- fetch-chapters | ISC-2 | fetch-toc | yes (parallel per chapter cluster)
- map-and-gap | ISC-3,4,5 | fetch-chapters | no
- write-doc | ISC-6,7,8 | map-and-gap | no

## Decisions

- 2026-07-09: Deliverable = workflow spec + gap map (Osama's pick); source = fetch online + cite (Osama's pick).
- 2026-07-09: E5 ISC-floor (≥256) relaxed — the deliverable is a sourced research-synthesis doc, not a complex app; ~8 substantive ISCs (one per methodological cluster + anti-criteria) fully cover the ideal state. Show-your-math: inflating to 256 probes would be ceremony, not coverage.

## Changelog

- conjectured: the engine's meta-analysis stats likely use weak defaults (fixed-effect / DerSimonian-Laird).
  refuted_by: reading core/sr/stats/metacont.R + metabin.R — they use `random=TRUE, method.random.ci="HK"` (Hartung-Knapp), Hedges' g, Mantel-Haenszel, prediction intervals at n>=3.
  learned: the MA CORE is already Cochrane-aligned; the real gaps are upstream (single-source search, abstract-only extraction, RoB never run, single reviewer) and around it (NMA inconsistency checks, absolute effects, per-outcome RoB) — verifying before asserting changed the gap map materially.
  criterion_now: the doc credits the stats core as 🟢 and points the 🔴 fixes at search/screen/extract/rob/nma.

## Verification

- ISC-1: Handbook TOC fetched (v6.5, 2024) with real chapter numbers/URLs (Part 1 I–V, Part 2 1–15, Part 3 16–21).
- ISC-2: 7 parallel readers fetched + distilled Ch 2,3,4,5,6,7,8,9,10,11,12,13,14,15 with section-level citations.
- ISC-3: all 11 engine stages present in the map (grep ✓ search…manuscript).
- ISC-4: each stage row carries Cochrane-requires / engine-does / gap / severity (🔴9 🟠18 🟡10).
- ISC-5: prioritized punch-list (Critical/High/Medium, 18 items) each mapped to a stage + chapter citation.
- ISC-6: docs/methodology/cochrane-workflow.md written (145 lines, 79 chapter citations).
- ISC-7/8: distilled + cited throughout (no verbatim passages); every "Cochrane requires" carries a [Ch N §S.S] citation from this session's fetch. Agents noted the two-independent-assessor RoB citation is a MECIR standard to spot-verify — flagged in the doc, not asserted falsely.
