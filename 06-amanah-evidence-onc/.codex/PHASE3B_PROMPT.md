# Codex Task — Phase 3b: Meta-regression + Median/IQR Imputation Utilities

## Mission
Add two high-value capabilities that fix KNOWN weaknesses: (1) meta-regression to explain heterogeneity by a study-level moderator, and (2) pure-Python imputation utilities that recover continuous studies currently dropped because they report median/IQR/range instead of mean/SD. Follow the EXISTING patterns exactly. Keep everything honesty-gated. Do not refactor unrelated code. Bounded scope — see "Out of scope".

## Context — existing patterns you MUST follow (do not reinvent)
- R + Python stats pattern: `core/sr/stats/metaprop.R`/`metahr.R` (R CLI contract `<input_csv> <results_csv> <forest_png> <label>`), `core/sr/stats/meta.py` (`run_*` wrappers + `r_available()`/`metabin_available()` package gates), `core/sr/stats/prepare.py` (`hr_rows`/`cont_rows`/`bin_rows` with `_real`, parse helpers, `canonical_subgroup`, and exclude-and-count), `core/sr/stats/run.py` (`meta_analyze` method-gated dispatch), `core/sr/config.py` (`poolable_fields`/`binary_2x2_fields`/`continuous_fields` readers).
- Tool registry: `core/tools/builtin.py` (deterministic tool reading `extractions.json`, writing a stats artifact; `metabin`/`metacont` are the templates), `core/tools/spec.py`.
- Honesty + provenance: the extraction layer already has an `ESTIMATED` flag for derived values (`core/sr/models.py` `ExtractedValue.flag`). Imputed numbers must be flagged the same spirit — clearly marked derived, with the method named. Reuse, never weaken: exclude-and-count, fail-closed gates, no fabricated estimate.
- metafor is already a dependency (used by `metaprop.R`). Meta-regression uses `metafor::rma(yi, vi, mods=...)`.

## Build

### 1. Imputation utilities (PURE PYTHON — no R) — recover dropped continuous studies
- New module `core/sr/stats/impute.py` with well-established, citable formulas (put the citation in each docstring):
  - `sd_from_se(se, n)`; `sd_from_ci(lo, hi, n, conf=0.95)` (t- or z-based); `sd_from_range(lo, hi, n)` (Hozo 2005).
  - `mean_sd_from_median_iqr(median, q1, q3, n)` — Wan 2014 (mean) + Wan 2014/Luo 2018 (SD).
  - `mean_sd_from_median_range(median, lo, hi, n)` — Wan 2014 / Hozo 2005.
  - Each returns `(mean, sd, method_label)`; raise/return None on non-finite or n<2 (never guess).
- Wire into `core/sr/stats/prepare.py` `cont_rows(...)`: for each study, try the reported mean/SD first; if either is missing/unparseable BUT median + (IQR q1,q3) or (range lo,hi) + n are present, impute via `impute.py`, set the row's mean/SD, and FLAG the row as imputed (carry `imputed: true` + `method` through to the row dataclass and the tool output). Studies still lacking any usable form remain excluded-and-counted (unchanged).
- `core/sr/config.py`: extend `continuous_fields()` pairing to accept OPTIONAL median/iqr/range field names (e.g. `median_tx, q1_tx, q3_tx, lo_tx, hi_tx`) alongside the required mean/sd pair, so imputation is config-driven per vertical.
- Output: the `metacont` tool's `stats_continuous.json` must report how many studies were imputed and by which method (transparency — the human sees which numbers are derived).

### 2. Meta-regression — explain heterogeneity by a moderator
- `core/sr/stats/metareg.R` via `metafor::rma(yi, vi, mods = ~ moderator)`. Input CSV: `pmid, yi, vi, moderator`. Output CSV: `term, estimate, se, ci_lb, ci_ub, pval` for the intercept + moderator, plus a summary row/columns: `QM, QM_p` (test of moderators), `QE, QE_p` (residual heterogeneity), `I2_resid, R2` (variance explained), `k`. No forest PNG required (optional bubble plot if trivial).
- `core/sr/stats/meta.py`: add `run_metareg(rows, moderator_label, ...)` + `metareg_available()` (metafor). Dataclass `MetaRegResult`.
- `core/sr/stats/prepare.py`: add `metareg_rows(extractions, outcome_field, moderator_field, ...)` that derives per-study `(yi, vi)` for the chosen outcome by REUSING existing logic — for a `log_effect` outcome use the same logHR+variance path as `hr_rows`; for a `continuous` outcome use the SMD/MD effect+variance from `cont_rows`. Attach the moderator value parsed from the study's extraction. Exclude-and-count any study missing the effect OR the moderator. v1: support a CONTINUOUS (numeric) moderator; allow a categorical moderator but keep the v1 test on a numeric one.
- HONESTY GATE (critical — Cochrane explicitly warns here): meta-regression is unreliable with few studies. If `k < 10` (configurable threshold, default 10) per moderator, still compute BUT return a prominent caveat (`reliable: false`, note "k=<n> below the recommended ≥10 studies per covariate — exploratory only, do not over-interpret"). If `k < 4`, return `available: false` / "insufficient studies for meta-regression" and do not fit. Never present a small-k meta-regression as conclusive.
- Register a deterministic `meta-regression` tool in `core/tools/builtin.py`: reads `extractions.json` (+ `screening_merged.json` for subgroup if needed), args `--outcome <field> --moderator <field>`, writes `stats_metareg.json`. Scorecard `internally_tested=True, externally_validated=False`.

## Honesty gates (non-negotiable)
- Imputed mean/SD always flagged as derived with the method named; counted/reported, never silently substituted.
- Meta-regression: `k<4` → not fitted; `4≤k<10` → fitted but `reliable: false` with the small-k caveat.
- `*_available()` fail-closed → offline tests pass without R; pure-Python imputation always available.
- Exclude-and-count anything unparseable. Do not mark anything `externally_validated=True`.

## Tests (offline-first; live R gated)
- `impute.py` (pure Python, no gate): reproduce the worked examples from Wan 2014 (mean & SD from median+IQR and median+range) within tolerance; `sd_from_ci`/`sd_from_se`/`sd_from_range` hand-checks; None on n<2 / non-finite.
- `cont_rows`: a study with only median+IQR+n is now INCLUDED (imputed, flagged), whereas one with no usable spread stays excluded-and-counted; the imputed flag + method propagate to output.
- Meta-regression honesty: `k<4` → not fitted; `k=6` → fitted with `reliable: false`; `k≥10` → `reliable: true`.
- R-gated (`@pytest.mark.skipif(not metareg_available())`): a known dataset where the moderator explains heterogeneity returns the expected coefficient sign + significant QM within tolerance.
- Tool contract tests (registered, correct artifacts, `externally_validated=False`); extend the offline tool-chain test so `meta-regression` runs through `runner.run_tool` on a small fixture.
- Manuscript non-crash with the new outputs present (mirror the Phase-3 manuscript-tolerance test).

## Verification before done
- `python -m pytest -q -m "not live"` fully green (no regressions to the existing 330).
- With CRAN R (`SR_RSCRIPT=/Library/Frameworks/R.framework/Resources/bin/Rscript`): the R-gated meta-regression test runs (not skips) and passes.
- `python -m core.tools list` shows `meta-regression`; `python -m core.tools describe meta-regression`.

## Out of scope — do NOT build in this PR (Phase 3c+)
PET-PEESE / Peters' test; `metarate`/`metainc`/`metamean`/`metacor`; GLMM (`rma.glmm`); multivariate/multilevel (`rma.mv`); cumulative MA; TSA; diagnostic-test-accuracy; RVE; SUCRA/node-splitting; categorical-moderator-heavy meta-regression beyond the basic case; any web/API/auth/DB changes. List these as the Phase-3c backlog in the PR description.

## Implementation order
1. `impute.py` + pure-Python tests (fast, no R).
2. Wire imputation into `cont_rows` + config optional median/IQR/range fields + flag propagation + tests.
3. `metareg.R` + `run_metareg` + `metareg_rows` + honesty gate + offline + R-gated tests.
4. Register `meta-regression` tool + contract test + extend chain test + manuscript-tolerance test.
5. Full offline suite green; R-gated green on CRAN R; PR description with Phase-3c backlog.

## Cross-cutting (validated workflow)
Reuse existing code; match surrounding style; conservative scorecards; offline tests must not need network/R/keys. After you push, Claude/PAI runs the verification pass (full offline suite + R-gated tests on CRAN R + the honesty gates: imputed-flagging, small-k meta-regression caveat, exclude-and-count).
