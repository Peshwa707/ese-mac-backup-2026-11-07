# Codex Task — Phase 0: Benchmark scaffolding (reusable machinery + offline tests; NO live runs)

## Mission
Build the `benchmark/` package that will let us blind-benchmark the engine against 50 published oncology meta-analyses. Phase 0 builds ONLY the reusable, fully offline-tested machinery — sampling-frame builder, answer-key schema + drafter, scoring harness, aggregate reporter, run driver, and the PAI harness-fulfillment helper. **No live API calls, no real study selection, no engine runs** — those are later phases. Propose your PLAN first; after approval, implement on a branch; Claude/PAI verifies.

Branch from `origin/main` (HEAD `e26e38a`). Baseline: **368 offline tests green**.

## Background (the program this serves)
For each published meta-analysis we will: extract its PROTOCOL (not its results) → compile an engine vertical → run `full-review` fresh via the harness → compare engine outputs to a sealed "answer key" of the published results → adjudicate divergences honestly → publish a repo-visible benchmark report. Phase 0 is the code that makes that repeatable. Honesty invariants are unchanged and must be respected throughout.

## Confirmed seams to REUSE (do not reinvent)
- **Run-output loaders** — `core/tools/sr_io.py`: `load_stats(run_dir)` (→ namespace with `outcomes, hr_outcomes, bin_outcomes, cont_outcomes`), `load_tool_extractions(run_dir)`, `load_tool_screening(run_dir)`, `modality_by_pmid(run_dir)`, `load_run_bundle(run_dir, vertical)`. `includes.json` is a raw `[pmid,...]` list.
- **Pooled-result schemas** — `core/sr/stats/{run.py,meta.py}`: outcome wrapper `{field,label,results[],n_pooled,n_excluded,forest_png}`; `PoolResult{group,k,prop,ci_lb,ci_ub,I2,...}`; `HRPoolResult{group,k,hr,ci_lb,ci_ub,I2}`; `BinPoolResult{group,k,measure,estimate,ci_lb,ci_ub,rd,number_needed,I2}`; `ContPoolResult{group,k,measure,estimate,ci_lb,ci_ub,I2}`.
- **Harness protocol** — `core/tools/harness.py`: requests at `run_dir/harness/<stage>/<source>/REQUEST.md`, batch inputs under `<stage>_in/`, expected outputs `screen_batch_*.json` / `extract_idx_*.json`; `collect_results` applies guardrails (drop hallucinated PMID, missing→uncertain). Result schemas `ScreeningResult` / `ScreeningBatch` / `ExtractedStudy` in `core/sr/models.py`.
- **Protocol compiler** — `python -m core.sr.protocol --draft draft.txt --name <v> [--dest DIR]` or `--spec spec.json`; `ProtocolSpec` in `core/sr/protocol/spec.py`; `validate_vertical` in `core/sr/protocol/validate.py` (hits PubMed esearch).
- **PubMed enumeration** — reuse the esearch util `validate.py` already uses (or the existing pubmed connector). Do NOT write a new HTTP client.
- **Precedent** — `scripts/benchmark_local.py`, `scripts/benchmark_cr.py` (style + R pooling).
- **LLM seam** — `core.llm.client.parse(schema, system, user, stage=...)`; tests monkeypatch this. claim-check pinned cloud via `_CLOUD_ONLY_STAGES`.

## Build (each item ships with offline tests; mock the LLM, use committed fixtures)
1. **`benchmark/sample.py`** — sampling frame.
   - `STRATA` config: 6 domains with target n (8/8/8/8/9/9 = 50) + a PubMed query template each (`Meta-Analysis[Publication Type]` + domain filter + date window).
   - `enumerate_frame(stratum)` → candidate PMIDs via the existing esearch util (mock in tests).
   - `seeded_sample(pmids, n, seed)` — **deterministic** (fixed seed → identical pick).
   - `benchmark_set.json` schema (entries: id, domain, pmid, doi, title, status) + PRISMA-style selection-log writer. Phase 0 = machinery + schema only; do NOT freeze a real 50-set.
   - Tests: fixed seed → deterministic; strata sum to 50; selection log round-trips.
2. **`benchmark/keys.py`** (+ `benchmark/keys/` dir) — answer-key model + drafter.
   - Pydantic `AnswerKey`: published PICO; `included_pmids[]`; `outcomes[]` each `{name, measure, model, k, estimate, ci_lb, ci_ub, i2, source_location}`; optional GRADE; `verified: bool` (default **False**).
   - `draft_answer_key(paper_text)` → `AnswerKey` via `llm.parse` (cloud; mocked in tests). NEVER auto-set `verified=True`.
   - `load_key(path)` validates and **fails closed on an unverified key at scoring time** (warn/raise).
   - Tests: schema validation; mocked drafter returns `verified=False`; loader rejects malformed/unverified.
3. **`benchmark/score.py`** — the scoring harness (core deliverable). `score_run(run_dir, key)` → a `ScoreCard` with 5 axes:
   - (1) **retrieval**: recall `|engine∩pub|/|pub|`, precision `|engine∩pub|/|engine|` over included PMIDs; ALSO report after collapsing engine trial-dedup (`trial_id`/`member_pmids`) — the Liang lesson (43 reports vs 11 trials).
   - (2) **pooled-estimate concordance** per matched outcome: direction agreement, significance agreement (CI excludes null), relative point-estimate delta, CI-overlap (engine est ∈ pub CI and vice-versa), I² delta. Match engine→key outcomes by field/measure/subgroup; report unmatched on both sides.
   - (3) **conclusion concordance**: qualitative label {effect / no-effect / uncertain} from engine narrative vs key; if locked/undrafted → "withheld".
   - (4) **GRADE concordance** where the key has it.
   - (5) **honesty audit**: fabricated-value count (must be 0); NR/excluded tallies from extractions.
   - Pure/deterministic given `(run_dir, key)` — NO LLM. Reuse `sr_io` loaders.
   - **Keystone test**: commit a small trimmed fixture run-folder under `benchmark/tests/fixtures/run_mm_bcma/` (trimmed `stats_configured.json`, `includes.json`, `extractions.json`, `screening_merged.json` taken from a real mm-bcma run — do NOT depend on the gitignored `runs/` dir) + a hand-written Liang-style key (CR ≈ 54%/35%); assert recall/precision computed, CR direction + CI-overlap concordant, I² delta present, fabrication=0. Plus synthetic edge cases (no overlap; CI non-overlap; unmatched outcomes).
4. **`benchmark/report.py`** — aggregate reporter. `aggregate(scorecards, date)` → per-domain + overall concordance + divergence-cause taxonomy counts (`retrieval-gap|extraction-gap|true-method-difference|published-error`, default `unadjudicated`); emits `benchmark/reports/<date>-summary.{md,json}`. Pass `date` IN (no `now()`/`today()` inside — keep deterministic). Tests: fixture scorecards → expected rates; md+json round-trip.
5. **`benchmark/run_driver.py`** — thin orchestration (no live calls in Phase 0). `compile_vertical(draft_path, name)` wraps the protocol compiler; `run_full_review(vertical, run_dir, harness_source)` wraps the recipe runner with `--harness`. Phase 0: structure + a dry-run/`--plan` path. Tests: builds the right invocation (mocked).
6. **`benchmark/harness_fulfill.py`** — PAI harness-fulfillment helper. Given a `run_dir`, detect a pending request (`harness/<stage>/<source>/REQUEST.md` + missing expected outputs), read batch inputs, call `llm.parse` to produce schema-valid `ScreeningResult`/`ExtractedStudy` JSON, write to the exact expected filenames so a re-run resumes. Reuse `core/tools/harness.py` for paths/expected names; do NOT bypass `collect_results`. The fulfiller obeys all REQUEST.md rules (cite-or-omit, NR, no fabrication, echo PMIDs) and never sees/injects published results. Tests: fixture pending request + mocked `llm.parse` → schema-valid outputs at the right names; a subsequent `collect_results` accepts them AND still drops a hallucinated PMID + recovers a missing one as uncertain.

## Honesty / safety (unchanged, non-negotiable)
No fabrication anywhere; the honesty-audit axis asserts fabrication=0. Answer keys are unverified until a human flips `verified`. The fulfiller respects every REQUEST.md guardrail and never sees published results. No gate weakened; claim-check stays cloud-only.

## Out of scope (later phases — note as backlog, do NOT do)
Real 50-study selection/freeze (Phase 2); writing real answer keys (Phase 3); compiling the 50 verticals (Phase 4); any LIVE engine/harness run (Phase 1+). **No live API calls in this PR.**

## Verification before done
- `python -m pytest -q -m "not live"` fully green (368 baseline + new). The scoring test against the committed mm-bcma fixture is the keystone — it proves the harness works on a real engine run's artifacts.
- `git diff --check` clean. PR with a short map of `benchmark/` + remaining phases.

## Process
Propose your PLAN first (file list + the `ScoreCard` data model + test list) and STOP for review. After approval, implement; Claude/PAI verifies (full suite + scoring metrics correct on the fixture + honesty preserved).
