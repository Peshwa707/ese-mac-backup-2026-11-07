# Codex Prompt — Phase 3: Binary & Continuous Meta-Analysis + Modern Heterogeneity Reporting

## Mission
Add the highest-frequency net-new meta-analysis methods to the engine, following the EXISTING `core/sr/stats/` pattern exactly, and register the two new analyses as maturity-dialed tools. Scope is deliberately bounded (see "Out of scope"). Do not refactor unrelated code. Every new estimate must be reproducible R math, honesty-gated, and never fabricated.

## Context — existing patterns you MUST follow (do not reinvent)
The stats layer already has a proven one-R-script-per-method pattern. Copy it.
- R scripts: `core/sr/stats/metaprop.R`, `core/sr/stats/metahr.R` — each takes `<input_csv> <results_csv> <forest_png> <label>`, reads a CSV, writes a results CSV + forest PNG. Replicate this exact I/O contract.
- Python wrappers + result dataclasses: `core/sr/stats/meta.py` — `run_metaprop`/`run_metahr`, `r_available()` (checks `requireNamespace`). Add `run_metabin`/`run_metacont` the same way, plus `metabin_available()`/`metacont_available()` checking `requireNamespace('meta')`.
- Row builders + honest exclusion: `core/sr/stats/prepare.py` — `outcome_rows`/`hr_rows` exclude-and-count any unparseable row, and `canonical_subgroup(raw, subgroup_map)` normalizes the subgroup axis. New row builders must use the same exclude-and-count discipline and `canonical_subgroup`.
- Dispatch + config: `core/sr/stats/run.py` `meta_analyze(...)` iterates `cfg.poolable_fields()` (`{"proportion": [...], "log_effect": [...]}`) from `core/sr/config.py`. Extend config + dispatch for the new pool kinds.
- Vertical markers: outcome fields are declared in `verticals/*/extraction_schema.yaml` with `pool: proportion|log_effect` + `label`. You will add `pool: binary_2x2` and `pool: continuous`.
- Tool registry: `core/tools/builtin.py` registers tools (`pool-proportions` is the template — a deterministic tool reading `extractions.json`, writing a stats artifact). `core/tools/spec.py` defines `Tool`/`BackendPolicy`/`Scorecard`.
- Honesty invariants (reuse, never weaken): retrieval-grounded, exclude-and-count, fail-closed `r_available()` gating, no pooled number without ≥2 contributing studies, no fabricated estimate. See `core/sr/audit.py` ethos.

## Prerequisite
The new analyses need the R `meta` package (and `metafor` for later phases). Tools must DEGRADE HONESTLY when it is absent (return `available: false` with a clear note — exactly like the existing R-gated tools), so offline tests pass without R. Document the install (`Rscript -e 'install.packages(c("meta","metafor"))'`) in the script headers and PR description.

## Build (scoped to Phase 3)

### 1. `metabin` — binary 2×2 outcomes → RR / OR / risk-difference / NNT
- `core/sr/stats/metabin.R` via `meta::metabin`. Input CSV columns: `pmid, subgroup, events_tx, n_tx, events_ctrl, n_ctrl`. Args mirror `metaprop.R`. Output CSV per group (overall "ALL" + each subgroup): `group, k, measure, est, ci_lb, ci_ub, rd, rd_lb, rd_ub, nnt, nnt_lb, nnt_ub, Q, I2, I2_lb, I2_ub, tau2, pi_lb, pi_ub`. Forest PNG when k≥2.
- Default pooling method `MH` (Mantel-Haenszel — Cochrane default for binary); also accept `Inverse` and `Peto` (rare events) via an arg. Random + common effect; report random by default.
- `sm` (summary measure) selectable: `RR` (default) or `OR`.
- NNT honesty gate (critical): compute from the risk difference. If the RD confidence interval crosses 0 → NNT = "not estimable". If RD indicates harm → report as NNH, clearly labeled. Never emit a bare NNT when direction is uncertain. Prefer `meta`'s own `nnt()` where available; otherwise 1/RD with CI propagated.
- `core/sr/stats/prepare.py`: add `BinRow` + `bin_rows(extractions, fields, modality_by_pmid, subgroup_field, subgroup_map)` that pulls the four 2×2 counts from paired extraction fields; exclude-and-count any row missing/!parseable a cell.
- `core/sr/config.py`: add `binary_2x2_fields()` reading `pool: binary_2x2` + a `pair:` linking the four count field names + `label`.
- `core/sr/stats/run.py`: dispatch binary outcomes into `StatsResult` (new `bin_outcomes` list), same structure as proportions.

### 2. `metacont` — continuous outcomes → Mean Difference + Standardized Mean Difference
- `core/sr/stats/metacont.R` via `meta::metacont`. Input CSV: `pmid, subgroup, n_tx, mean_tx, sd_tx, n_ctrl, mean_ctrl, sd_ctrl`. Output: `group, k, measure, est, ci_lb, ci_ub, Q, I2, tau2, pi_lb, pi_ub`. Forest PNG when k≥2.
- `sm` selectable: `MD` and `SMD` (Hedges' g, the bias-corrected default for SMD). Produce both when requested.
- `prepare.py`: add `ContRow` + `cont_rows(...)`, exclude-and-count when any of the six values is missing/unparseable.
- `config.py`: add `continuous_fields()` reading `pool: continuous` + the six paired field names + `label`.
- `run.py`: dispatch into a new `cont_outcomes` list.

### 3. Modern heterogeneity + robustness reporting (apply to the NEW tools; cheap retrofit to existing)
Every pooled result in the new R scripts must report: **Cochran's Q, I² (with CI), τ²**, and a **prediction interval** (`prediction=TRUE` in `meta`, only when k≥3 and τ² is estimable — else emit nulls, do not fabricate). Use the **Hartung-Knapp (HKSJ)** random-effects CI (`method.random.ci="HK"` / `hakn=TRUE`) when k is small (auto for k≥2; this fixes the known small-k CI problem the benchmarking surfaced). Where it is a one-line addition, also enable `prediction=TRUE` + HKSJ in `metaprop.R`/`metahr.R` outputs — but do NOT restructure those scripts beyond adding the columns.

### 4. Register the two new tools
In `core/tools/builtin.py`, register `metabin` and `metacont` as deterministic tools (kind="deterministic", no brain), reading `extractions.json` (merged-else-plain, like `pool-proportions`), using `screening_merged.json` + vertical config for the subgroup axis, writing `stats_binary.json` / `stats_continuous.json`. Scorecard: `internally_tested=True`, `externally_validated=False` (NO external-validated flag until a published-benchmark check passes — keep the trust gate honest).

## Honesty gates (non-negotiable)
- `r_available()`/`*_available()` fail-closed → `available: false` + reason; offline tests pass without R.
- No pooled estimate with k<2. Prediction interval only k≥3 + estimable τ². 
- NNT "not estimable" when RD CI crosses 0; NNH labeled when harmful.
- Exclude-and-count every unparseable row (never coerce/guess); report the excluded count.
- Do not mark anything `externally_validated=True`.

## Tests (offline-first; live R gated)
- Offline (monkeypatch `metabin_available`/`metacont_available` → False): both tools/degrade honestly, no crash, `available: false`.
- Hand-calculation checks gated with `@pytest.mark.skipif(not <available>())`: a known 2×2 reproduces a hand-computed RR + NNT; known means/SDs reproduce a hand-computed SMD (Hedges' g) within tolerance.
- `bin_rows`/`cont_rows`: parse a clean row; exclude-and-count a row with a missing cell.
- NNT honesty: RD CI crossing 0 → "not estimable".
- Prediction interval + HKSJ columns present when k≥3 / k≥2; null/absent when k too small.
- Tool contract tests (registered, correct artifacts, `externally_validated=False`), mirroring `tests/test_tools_builtin.py`.
- Extend the offline chain test so `pool-proportions` siblings (`metabin`/`metacont`) run through `runner.run_tool` against a small `extractions.json`.

## Verification before done
- `python -m pytest -q -m "not live"` fully green (no regressions to the 320 existing).
- `python -m core.tools list` shows `metabin` + `metacont` (kind deterministic, validated `internal`).
- `python -m core.tools describe metabin`.
- Live (only if R `meta` installed): the hand-calc RR/NNT and SMD tests pass.

## Out of scope — do NOT build in this PR (later phases)
Meta-regression (`metafor::rma` moderators); influence/outlier diagnostics (Baujat/Cook); PET-PEESE / Peters' test; `metarate`/`metainc`/`metamean`/`metacor`; GLMM (`rma.glmm`); SD/median-from-IQR imputation utilities (Wan/Luo); multivariate/multilevel (`rma.mv`); cumulative MA; TSA; diagnostic-test-accuracy (bivariate/HSROC); RVE; SUCRA/node-splitting for NMA; any web/API/auth/DB changes. List these in the PR description as the Phase-3b backlog; do not implement.

## Implementation order
1. `metabin.R` + `run_metabin` + `bin_rows` + config `binary_2x2_fields` + run.py dispatch + offline+gated tests.
2. `metacont.R` + `run_metacont` + `cont_rows` + config `continuous_fields` + run.py dispatch + tests.
3. Heterogeneity/PI/HKSJ columns in the two new scripts (+ cheap retrofit to metaprop/metahr).
4. Register `metabin` + `metacont` tools + contract tests + extend the chain test.
5. Full offline suite green; update PR description with the install note + Phase-3b backlog.

## Cross-cutting (from the validated workflow)
Reuse existing code; match surrounding style; keep the honesty invariants; conservative scorecards; offline tests must not need network/R/keys. After you push, Claude/PAI runs the verification pass (full suite + the new gated R tests where R is present + a published-RR/NNT benchmark before any `externally_validated` flip).
