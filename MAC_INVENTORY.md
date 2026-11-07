# Mac inventory of Evidence Synthesis Engine (ESE) copies

Snapshot taken 2026-11-07 on the Mac. Backup branch: `backup/mac-2026-11-07`.
Test runs were done 2026-11-07 with `python3 -m pytest -q -p no:cacheprovider` (miniconda python3). Runs marked "stopped at first failure" used `-x`, so their pass counts are partial.

## Versions found

| Folder (under `~`) | Branch | Last commit | Uncommitted | GitHub remote | Tests (this snapshot) |
|---|---|---|---|---|---|
| `Projects/evidence-synthesis-engine` | main | 2026-08-17 | 2 files | `Peshwa707/evidence-synthesis-engine` (PUBLIC) | 514 passed |
| `Projects/ese-extraction-fix` (worktree of the above) | fix/clonidine-extraction-pairs | 2026-07-11 | 0 | same PUBLIC repo | 514 passed |
| `Projects/evidence-synthesis-engine-neurodevelopmental-review` (worktree of the above) | codex/neurodevelopmental-mismatch-review-20260729 | 2026-07-10 | 36 files | same PUBLIC repo | stopped at first failure: 294 passed, 1 failed (`test_prompt_freeze.py::test_screening_prompt_fingerprint_is_frozen`; the screening prompt changed after the freeze) |
| `Projects/evidence-synthesis-engine-screen-prove` | codex/ai-only-screen-prove | 2026-08-17 | 8 untracked docs | `Peshwa707/evidence-synthesis-engine-screen-prove` (private) | 348 passed |
| `Projects/evidence-synthesis-engine-webui` | codex/webui-offline-build | 2026-08-30 | 40 files | none | 675 passed |
| `Projects/ese-engine` | main | 2026-06-28 | 0 | none | stopped at first failure: 1 failed because the `openai` module is not installed here (environment gap, not a code finding) |
| `Projects/onco-evidence-engine` (older name) | main | 2026-06-17 | 2 files | `Peshwa707/onco-evidence-engine` (private), plus two amanah remotes | 649 passed, 1 skipped |
| `onco-evidence-engine` (home folder) | feat/onco-evidence-engine | 2026-06-04 | 0 | `Peshwa707/onco-evidence-engine` (private) | not run |
| `Projects/onco-evidence-engine-neurodevelopmental-review` | codex/neurodevelopmental-mismatch-review | 2026-06-16 | 0 | same private repo | not run |
| `Projects/amanah-evidence-onc` | feat/screening-ensemble-self-consistency | 2026-06-17 | 3 untracked | `Peshwa707/amanah-evidence-onc` (private) | 685 passed, 1 skipped |

Also on the Mac: `.codex/worktrees/*` (clones of the screen-prove repo on `codex/*` branches, all clean, last commits 2026-08-20 to 2026-08-23), `Documents/Codex/2026-08-18-…` (screen-prove clone on `codex/pooling-v1`), and non-git copies in `Documents/EvidenceEngine`, `Documents/EvidenceEngine-Codex-Handoff` and `Documents/SOA-Evidence-Legacy-Vault`. These were not tested.

## What each line does

- **evidence-synthesis-engine** (public): the main engine for systematic reviews and meta-analyses: search, screening, extraction, risk of bias, synthesis, and vertical configs.
- **screen-prove**: a smaller offline "screen and prove" tool split out of the engine in Aug 2026. It imports citation exports, deduplicates, runs a cached AI screening adapter, compares against published labels, and replays runs for audit. It has no remote calls by design.
- **webui**: a private offline web interface for screen-prove.
- **ese-engine**: an earlier parallel carve of the engine (June 2026) with its own LLM client layer.
- **onco-evidence-engine / amanah-evidence-onc**: the earlier oncology-named predecessors of the engine.

## Screen-and-Prove work packages

The repo's own records number the work packages differently from the way they were described to me, so these are the repo's labels (from `docs/reviews/wp-0N-handoff.md` and `wp-0N-verdict.md`):

| Repo label | Scope in the repo | Status in the repo |
|---|---|---|
| WP-01 | repository controls, hashing, protocol locks, Task Envelope v1 | handoff and verdict present |
| WP-02 | RIS/NBIB import **and** deterministic deduplication (one package) | verdict: APPROVE after re-review; 7 focused tests passed then |
| WP-03 | cached screening adapter and adjudication toggle | verdict: APPROVE after re-review; 21 focused tests passed then |
| WP-04 | sealed comparison, package hashing, replay, CLI | verdict: APPROVE after re-review; 30 focused tests passed then |
| WP-05 | `screen-prove doctor` readiness command | handoff present, **no verdict file**; `tests/test_doctor.py` exists and the full suite passes |

So, mapped to the names used in the request: import and dedup are done together (repo WP-02); the screening adapter is done (repo WP-03); comparison and replay are done (repo WP-04). WP-05 in the repo is the doctor command, not comparison and replay, and it has no recorded verdict. The full suite (348 tests) passes on this branch.

## Caveats

- On this Mac, the local `codex/ai-only-screen-prove` is 12 commits behind `origin/codex/ai-only-screen-prove`. The GitHub copy is newer for that branch.
- Test results are from one run each on this machine; nothing was fixed to make them pass.
