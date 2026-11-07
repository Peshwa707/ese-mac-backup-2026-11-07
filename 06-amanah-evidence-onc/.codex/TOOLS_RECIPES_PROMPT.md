# Codex Task — Complete the Tool Catalog + Recipes (the end-to-end product)

## Mission
Wrap the remaining engine stages as tools (`risk-of-bias`, `grade`, `narrative`, `manuscript`), then add a RECIPE layer that runs tools in a fixed validated order, and ship two recipes: **Quick Look** (fast triage) and **Full Review** (the complete pipeline). The conclusion hard-lock and all honesty gates must hold. Wrap EXISTING code — do not reimplement the stages. This is large; you MAY split into two PRs (PR-A = the 4 tools; PR-B = the recipe layer, which depends on the tools) — recipes need the tools to exist first.

## Context — existing code to WRAP (do not reinvent)
- Tool platform: `core/tools/` — `spec.py` (`Tool`/`BackendPolicy`/`Scorecard`), `registry.py` (`register`), `runner.py` (`run_tool`), `builtin.py` (existing tools: `search`/`screen`/`extract`/`pool-proportions`/`metabin`/`metacont`/`meta-regression` are your templates), `cli.py`. Tools are functions over a run-folder (read input artifact(s) → write output artifact).
- Risk of bias: `core/sr/rob.py` (tool selection RoB2/ROBINS-I/NOS by design; per-domain LLM judgments). AI stage.
- GRADE: `core/sr/grade.py` `grade_outcomes(...)` — DETERMINISTIC rule-based certainty. Conclusion-bearing.
- Narrative: `core/sr/narrative.py` (`build_evidence_brief` grounded brief + citation validation) and the AI drafting + adversarial claim-check (`core/synthesis/claim_checker.py`). AI stage, conclusion-bearing; claim-check is cloud-only.
- Manuscript: `core/sr/manuscript/export.py` (`export_project`/`build_components`) — assembles MD/DOCX/PDF; `python -m core.sr.manuscript <run_dir>` is the existing entry. Deterministic assembly, conclusion-bearing rendering.
- HITL lock: `core/sr/signoff.py` (`is_locked`, `approve`, `DRAFT_CERTAINTY`, `DRAFT_BANNER`) + the run's `reviewer_signoff`. Built in Phase 1; `grade`/`narrative`/`manuscript` are conclusion-bearing and MUST honor it.
- Review profiles: `core/sr/profiles.yaml` + `core/sr/profile.py` (systematic/rapid/scoping/living, each declaring `phases`). REUSE these to define recipe phase-sets (Quick Look ≈ rapid; Full Review ≈ systematic).
- Honesty invariants: reuse exclude-and-count, cite-or-omit, fail-closed gates; never weaken.

## Build — Part A: the four tools

Register each in `core/tools/builtin.py` (or a sibling module) with the correct `BackendPolicy` + `Scorecard` (`internally_tested=True`, `externally_validated=False`), and a `conclusion_bearing` marker where noted (add a `conclusion_bearing: bool = False` field to `BackendPolicy` or `Tool` if one doesn't exist).

1. `risk-of-bias` — **AI** (cloud default). Reads `screening_merged.json` + `extractions.json`, runs the existing RoB assessment, writes `rob.json`.
2. `grade` — **deterministic**. Reads `extractions.json` + `rob.json` (+ screening for designs), runs `grade_outcomes`, writes `grade.json`. **Conclusion-bearing:** the rendered certainty must respect `signoff.is_locked(run)` — show `DRAFT_CERTAINTY` until a human sign-off exists; the computed value stays in the JSON.
3. `narrative` — **AI** (cloud default, **conclusion-bearing**). Reads the stats artifacts + `grade.json` + `rob.json` (the grounded brief), runs the existing narrative drafting + adversarial claim-check, writes `narrative.json`. The claim-check MUST stay cloud-only (it already is via `_CLOUD_ONLY_STAGES`); conclusions locked until sign-off.
4. `manuscript` — **deterministic** assembly. Reads ALL artifacts, runs `export_project`, writes the manuscript package (MD/DOCX/PDF). **Conclusion-bearing rendering** must be lock-gated (final conclusions/certainty render as DRAFT until sign-off — this already works in `assemble.py`; just make sure the tool passes the run's signoff state through).

Approval: extend the tool CLI so `run` accepts `--approve-conclusions "<reviewer>"`, which records a `ReviewerSignoff` (via `signoff.approve`) into the run before the conclusion-bearing tool renders.

## Build — Part B: the recipe layer

- `core/tools/recipes.py`: a `Recipe = {name, description, steps: list[(tool_name, args)], scorecard}`. `run_recipe(name, run_dir, args, override, posture)` runs each step via `runner.run_tool` IN ORDER, stops with a clear error if a step fails (and reports which step), and threads the run/sign-off state. A `RECIPES` registry mirrors the tool registry. Recipes carry a `Scorecard` (a recipe is validated end-to-end; `externally_validated=False` for now).
- **Full Review** recipe — the complete pipeline: `search → screen → fulltext → extract → risk-of-bias → grade → metabin/metacont/pool-proportions (whichever the vertical configures) → narrative → manuscript`. Conclusions remain locked until human sign-off.
- **Quick Look** recipe — fast TRIAGE: `search (narrow: a top-N/retmax cap arg) → screen → extract → pool (configured) → a short summary artifact`. CRITICAL honesty rule (per the product spec): Quick Look is triage-only — its output is loudly marked "rapid scan of the top N of M — NOT a complete review; run Full Review before relying on it", and it can **NEVER** emit a final conclusion or certainty, even WITH a sign-off. Only Full Review can be finalized. Encode this so a sign-off does not unlock a Quick Look conclusion.
- CLI: `python -m core.tools recipe list`, `python -m core.tools recipe run <name> <run_dir> [--vertical .. --retmax .. --approve-conclusions ..]`. `recipe list` shows each recipe + its steps + scorecard.

## Honesty gates (non-negotiable)
- `grade`/`narrative`/`manuscript` honor `signoff.is_locked` — final conclusions/certainty are DRAFT until a recorded human sign-off.
- Quick Look is triage-only: can NEVER finalize a conclusion, even with a sign-off; always marked rapid/incomplete.
- claim-check inside `narrative` stays cloud-only (never forced local).
- Tools fail-closed on missing inputs; `run_recipe` stops with a clear "step X failed" error, not a traceback dump for expected failures.
- All scorecards `externally_validated=False`.

## Tests (offline-first)
- Each tool: contract test (registered, correct in/out artifacts, backend policy, conclusion_bearing flag), mirroring `tests/test_tools_builtin.py`.
- `risk-of-bias`/`narrative` (AI): run with a mocked `llm.parse` (pattern in existing tests), assert the artifact is written.
- `grade` tool: certainty renders `DRAFT — awaiting Reviewer 2` without sign-off; full certainty after `--approve-conclusions` (reuse the Phase-1 `tests/test_sr_signoff.py` pattern).
- `narrative` tool: conclusions locked without sign-off; claim-check routes cloud even under forced local.
- `manuscript` tool: assembles without crashing; conclusion blocks DRAFT-gated without sign-off.
- Recipe: offline integration test running **Quick Look** AND **Full Review** through `run_recipe` with mocked PubMed + mocked LLM; assert each step's artifact exists, that Full Review's conclusions are DRAFT without sign-off, and that **Quick Look output is marked triage and carries NO final conclusion even when a sign-off is present**.
- `run_recipe` stops cleanly when a step fails (clear error naming the step).

## Verification before done
- `python -m pytest -q -m "not live"` fully green (no regression to the existing 342).
- `python -m core.tools list` shows all tools incl. `risk-of-bias`/`grade`/`narrative`/`manuscript`.
- `python -m core.tools recipe list` shows Quick Look + Full Review with their steps.
- With CRAN R, the R-gated stats tests still pass.

## Out of scope — do NOT build (later)
The autonomous agent (mode D); web UI; MCP; the PAI-harness backend; true async/background recipe execution (recipes run synchronously here — "runs in the background + notifies" is a UX concern for a later pass); Phase-3c statistics; any web/API/auth/DB changes. Note these as the backlog in the PR description.

## Implementation order
1. `risk-of-bias` tool + tests.
2. `grade` tool + HITL-lock integration + sign-off CLI flag + tests.
3. `narrative` tool (+ lock, claim-check cloud-only) + tests.
4. `manuscript` tool (+ lock-gated rendering) + tests.
   (PR-A could end here.)
5. `recipes.py` + `run_recipe` + recipe CLI + Full Review + Quick Look (triage-only) + recipe scorecards + integration tests.
   (PR-B.)
6. Full suite green; PR description(s) with the deferred backlog.

## Cross-cutting (validated workflow)
Reuse existing code; match surrounding style; conservative scorecards; offline tests must not need network/R/keys. Propose your PLAN first (single PR vs split A/B) for review before implementing. After you push, Claude/PAI runs the verification pass — full suite + the lock behavior on `grade`/`narrative`/`manuscript` + the Quick-Look-can-never-finalize rule + claim-check-cloud-only.
