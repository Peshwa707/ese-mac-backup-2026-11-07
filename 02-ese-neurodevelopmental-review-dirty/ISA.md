---
task: Fix engine bugs found during the antithrombin/pre-eclampsia SR run
project: evidence-synthesis-engine
effort: E3
phase: complete
progress: 12/12
mode: algorithm
started: 2026-07-09
updated: 2026-07-09
---

## Problem

The antithrombin/pre-eclampsia run surfaced four defects, one of them a silent
correctness catastrophe:

1. **Vertical not pinned to the run (CRITICAL).** `ese init --vertical X` does not
   persist X. `ese next`/`status`/`export` re-resolve the vertical from `ESE_VERTICAL`/
   `SR_VERTICAL` env, else `_DEFAULT_VERTICAL="systematic-review"` — which IS the
   MM-BCMA review. A run started for one PICO can be silently screened against another.
2. **Many modules re-resolve the vertical independently from env.** `grade_config()`,
   `high_rob_labels()`, `shared_or_vertical()` all call `resolve_vertical(None)`. Even a
   correct in-process `cfg` does not reach them — they read the env var.
3. **`ese export` crashes:** `'SRConfig' object has no attribute 'vertical'`
   (cli.py `load_run_bundle(run_dir, vertical=cfg.vertical)`).
4. **`_load_dotenv()` reads `<project>/.env`, not `~/.config/ese/providers.env`** — the
   documented key location — so LLM stages fail auth if only providers.env is populated.
5. **GRADE borrows the shared vertical's MM outcome list** when a vertical has no own
   grade_config.yaml (antithrombin graded myeloma outcomes, n_studies=0).

## Vision

A run is self-describing: once `ese init --vertical X` is done, every later command and
every in-process module operates on X with no env babysitting. Export works. A new
vertical grades its own outcomes. The next person can never silently screen one disease
against another's PICO.

## Out of Scope

- No git commit (repo has pre-existing uncommitted WIP; per Osama's "don't git-touch the engine").
- Not genericizing the shared systematic-review grade/rob config (it doubles as the live mm-bcma review config — changing it regresses myeloma).
- Not refactoring the frontend/idea path.

## Principles

- Fix the ingestion point: pin the vertical once, at the run, and propagate to the process env so every independent resolver agrees.
- Backward compatible: legacy runs without a persisted vertical still resolve via env/default.
- Runtime proof over typecheck: re-run the real pipeline and confirm the right PICO + working export.

## Constraints

- Python engine (existing); edit working tree on top of WIP, do not clobber pre-existing changes.
- RunState is pydantic; new field must default so old run_state.json still loads.
- Must not regress mm-bcma-early-relapse (verify its grade outcomes unchanged).

## Goal

Persist the vertical in run state and propagate it to the process env in every ese
subcommand; add `SRConfig.vertical`; load `~/.config/ese/providers.env`; give the
antithrombin vertical its own grade/rob config so it grades its own outcomes — all proven
by a fresh runtime re-run and the existing pytest suite.

## Criteria

- [ ] ISC-1: `SRConfig.vertical` returns the vertical dir name.
- [ ] ISC-2: `RunState` has a `vertical` field; `create_run` persists `cfg.vertical`.
- [ ] ISC-3: `run_state.json` for a fresh init contains the vertical name.
- [ ] ISC-4: `ese next <run>` with `ESE_VERTICAL` UNSET uses the run's persisted vertical (not the MM default).
- [ ] ISC-5: each of cmd_next/cmd_status/cmd_export sets `os.environ["ESE_VERTICAL"]` to the run's vertical so grade/rob/shared_or_vertical agree.
- [ ] ISC-6: `_load_dotenv` also loads `~/.config/ese/providers.env`; keys resolve without manual sourcing.
- [ ] ISC-7: `ese export` no longer raises the `cfg.vertical` AttributeError.
- [ ] ISC-8: antithrombin vertical has grade_config.yaml + rob_config.yaml with ITS outcomes; GRADE lists prolongation/birth-weight/etc., not myeloma.
- [ ] ISC-9: existing pytest suite still passes (no regression).
- [ ] ISC-10: mm-bcma-early-relapse grade outcomes unchanged (regression probe).
- [ ] ISC-11: fresh runtime re-run reproduces 4 antithrombin RCTs and a working export/manuscript path.
- [ ] ISC-12: Anti: no pre-existing WIP hunk in cli.py/status.py/engine.py/adapters.py reverted or clobbered.

## Test Strategy

- isc | type | check | tool
- ISC-1,2 | unit | import + attribute/field present | Bash python
- ISC-3,4 | runtime | init then `next` with env unset; inspect run_state.json + corpus topic | Bash
- ISC-6 | runtime | screen stage authenticates with no manual source | Bash
- ISC-7 | runtime | `ese export` exits without AttributeError | Bash
- ISC-8,10 | runtime | grade outcomes for both verticals | Bash python
- ISC-9 | suite | `pytest -q` affected modules | Bash
- ISC-11 | runtime | full pipeline re-run | Bash
- ISC-12 | diff | `git diff` hunks preserved | Bash

## Features

- name | satisfies | depends_on | parallelizable
- srconfig-vertical | ISC-1,7 | — | no
- runstate-persist | ISC-2,3,4 | srconfig-vertical | no
- cli-resolve-from-run | ISC-4,5,7 | runstate-persist | no
- dotenv-providers | ISC-6 | — | yes
- antithrombin-grade-config | ISC-8 | — | yes
- runtime-proof | ISC-9,10,11 | all | no

## Decisions

- 2026-07-09: Propagate resolved vertical to `os.environ["ESE_VERTICAL"]` in each subcommand — the only way the independent env-resolvers (grade/rob/shared_or_vertical) stay consistent without refactoring every call site.
- 2026-07-09: Fix bug 5 per-vertical (add antithrombin grade/rob config), NOT by genericizing the shared config, because the shared config is the live mm-bcma review config.

## Changelog

- conjectured: pinning the vertical in run state + resolving it in cmd_next/status/export fully fixes the wrong-PICO class.
  refuted_by: Forge cross-vendor review (GPT-5.4) — the standalone `python -m core.sr.manuscript` entry point and `load_run_bundle(vertical=None)` still resolved via env/default, and a pre-pinning legacy run exported silently as myeloma.
  learned: vertical resolution had MULTIPLE independent entry points; the fix must centralize on the run as source of truth, and the artifact path must fail closed when the run predates pinning.
  criterion_now: added `config.persisted_vertical()`; `load_run_bundle` + manuscript exporter honor it; `ese export` refuses a legacy run without `--vertical`.
- conjectured: preloading providers.env into the env is a harmless convenience.
  refuted_by: Forge — it bypassed `gateway.load_key`'s 0600/symlink/owner fail-closed checks (env is checked first).
  learned: a secret-file preload must replicate the same hardening or it silently disables a security control.
  criterion_now: `_load_dotenv(..., secure=True)` enforces symlink/perms/owner checks on providers.env.

## Verification

- ISC-1: `SRConfig.vertical` == 'antithrombin-preeclampsia' (python import check).
- ISC-2/3: RunState has `vertical` field; fresh init run_state.json persists 'antithrombin-preeclampsia'.
- ISC-4/5: `ese next` with ESE_VERTICAL + ANTHROPIC_API_KEY UNSET → corpus antithrombin=True, myeloma=False (274 records); `_cfg_for_run` set os.environ[ESE_VERTICAL] from the run.
- ISC-6: screen stage authenticated with NO manual source (providers.env auto-loaded, 0600 → no warning).
- ISC-7: `ese export` on the fresh run returned exit 0 → `.../antithrombin-preeclampsia/Antithrombin-Supplementation-...` (no AttributeError, correct title).
- ISC-8: GRADE graded 16 antithrombin outcomes ("Pregnancy prolongation", "Birth weight", ...); indirectness always_downgrade 0.
- ISC-9: full pytest 438 passed (twice — after core fixes and after Forge follow-ups).
- ISC-10: mm-bcma-early-relapse grade outcomes unchanged (orr/cr/mrd, always_downgrade 1) — no regression.
- ISC-11: fresh clean-env run reproduced 4 antithrombin RCTs end-to-end + correct export.
- ISC-12: git diff shows pre-existing WIP hunks intact alongside my additions; nothing clobbered.
- Forge follow-ups: Fix A (secure providers.env load), Fix B (load_run_bundle + manuscript exporter run-aware — cfg.vertical resolves from run), Fix C (export refuses legacy run without --vertical) all implemented + verified.
- ACCEPTED/DEFERRED (Forge #4/#5): the in-process `os.environ["ESE_VERTICAL"]` set in `_cfg_for_run` is safe for the one-shot CLI process (each `ese` call is a fresh process) but could leak across in-process reuse; full fix = thread vertical into grade/rob instead of the env bridge (larger refactor, deferred). RunState `vertical` added without a SCHEMA_VERSION bump — verified safe on this single-binary build (checksum verified before migrate; legacy files load with default ""); bump deferred.
