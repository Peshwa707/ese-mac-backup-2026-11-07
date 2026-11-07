# Codex Task — PAI-harness backend (agent-mediated; Claude Max OR ChatGPT Pro)

## Mission
Add a THIRD brain — `harness` — so the high-volume AI tools (`screen`, `extract`) can run on Claude via the PAI subscription OR on ChatGPT Pro / Codex (no per-token API cost) instead of the API key. The harness brain is **agent-agnostic and subscription-based**: the tool writes a MODEL-NEUTRAL work-request, pauses; a Claude OR a GPT agent fills the result into the run folder; re-running picks it up and continues. This is the FOUNDATION for a later dual-reviewer phase (Claude = Reviewer 1, GPT = Reviewer 2, human = final adjudicator), so build the **source dimension** in now even though this phase runs ONE source per stage. Reuse the EXISTING agent machinery. Bounded scope (see Out of scope).

## Context — existing machinery to REUSE (do not reinvent)
- The engine already has the agent path (this is how reviews ran with no API key):
  - `core/sr/agents/spec.py` — `StageSpec` (`SCREENING`, `EXTRACTION`), `render_prompt(cfg)`, `json_schema()`.
  - `core/sr/agents/runner.py` — `prepare_batches(...)` (write per-batch input files) and `collect(...) -> CollectResult` (read agent output back, schema-validated, with dropped/hallucinated-id guardrails).
  - `core/sr/agent_io.py` — `records_for_agents`, `dump_corpus`/`load_corpus`, `load_screening`.
  - `core/sr/agents/api_executor.py` — the NATIVE (API) executor. Harness is the agent-mediated ALTERNATIVE.
- Backend routing: `core/llm/client.py` (`backend_for`, `_CLOUD_ONLY_STAGES`, forced-backend ContextVar, `current_forced_backend`), `core/tools/posture.py` (`resolve_backend`, postures), `core/tools/runner.py` (`run_tool`).
- AI tools: `core/tools/builtin.py` `screen`/`extract` currently call `core/llm/client.parse(...)` (cloud/local in-process). Harness is handled at the TOOL level, NOT through `client.parse`.

## Build

### 1. `harness` backend + a SOURCE dimension
- Add `harness` as a recognized backend. Extend `core/tools/posture.py` with a `harness` posture that routes AI stages to `harness`; per-tool override `--harness`; plus a SOURCE selector `--harness-source claude|gpt` (default `claude`). The source is metadata on the harness run — it labels the request and is recorded in provenance; it does NOT change validation.
- Define the known sources in one place (e.g. `HARNESS_SOURCES = {"claude": "Claude (PAI subscription)", "gpt": "GPT (ChatGPT Pro / Codex)"}`) so a third can be added later.
- SAFETY: `cloud_only` stages (`claim_check`) can NEVER be `harness` (any source) or `local` — they must run on the reliable, unattended API path. `resolve_backend` raises if a `cloud_only` tool is forced harness; the `harness` posture leaves cloud_only on `cloud`.
- `harness` does NOT go through `client.parse`. Handled in the tool's run function.

### 2. Harness execution at the tool level (`screen`, `extract`), namespaced by source
When a tool's resolved backend is `harness` with source `<src>`:
- WRITE a MODEL-NEUTRAL work-request under `harness/<stage>/<src>/` in the run folder: the per-batch input files via `runner.prepare_batches`, plus a self-contained `REQUEST.md` containing the rendered prompt (`StageSpec.render_prompt`), the exact JSON schema (`StageSpec.json_schema`), the honesty rules (cite-or-omit; missing → NR; never fabricate), and the EXACT result filenames + location to write. The REQUEST.md must assume NOTHING Claude-specific — any capable agent (Claude OR GPT) must be able to fulfill it.
- CHECK for the agent's result in `harness/<stage>/<src>/` (e.g. `screen_batch_*.json` / `extract_idx_*.json`). If present and valid via `runner.collect`, load → write the tool's normal output (`screening_merged.json` + `includes.json` for screen; `extractions.json` + `extract_idx_*.json` for extract), stamping the source into the output/provenance, and return that path (DONE — same downstream contract as the API path).
- If absent, raise a dedicated `HarnessWorkPending` carrying the request path + expected result path + source + a one-paragraph instruction. The CLI (`_cmd_run`) and `run_recipe` catch it → CLEAN, non-error "AWAITING AGENT" pause: `awaiting harness agent (<src>) for '<tool>': fill <request path>, write <result>, then re-run.` Never a traceback for this expected pause; a recipe stops at that step cleanly and is resumable.
- IDEMPOTENT: re-running after the agent fills the result completes the step. The per-source namespacing means two sources' requests/results never collide — which is exactly what the dual-reviewer phase will need.

### 3. Agent contract (model-neutral, documented)
- Generate the per-run `REQUEST.md` (above) AND add a static `core/tools/HARNESS.md` describing the contract for EITHER assistant: where requests appear (`harness/<stage>/<src>/`), the schema to satisfy, where to write results, and the non-negotiable honesty rules (the agent is full Claude OR full GPT via subscription — same quality bar as the API; must still cite-or-omit, mark NR, never invent). Written so a ChatGPT/Codex agent and a Claude/PAI agent can both follow it verbatim.

### 4. Provenance (truthful, source-aware)
- Record the stage as backend `harness`, model = the SOURCE label (e.g. "Claude (PAI subscription)" or "GPT (ChatGPT Pro)") in `Provenance.stage_models` via `_record_stage`, so the AI-use statement truthfully reflects WHICH subscription agent did each stage.
- `ai_reporting`: harness sources are FULL models — do NOT flag them for elevated human review the way `local` is. (Note in the PR description: per-source benchmark validation is a later trust step, like local-model validation.)

## Honesty / safety gates (non-negotiable)
- `cloud_only` (`claim_check`) is never harness or local — always the API safety net.
- Harness results (any source) pass the SAME validation + guardrails as the API path (`runner.collect`: dropped-PMID recovery, hallucinated-PMID drop). No weaker validation because an agent produced them.
- Conclusion lock unaffected. `HarnessWorkPending` is a clean pause, never a swallowed error; unexpected errors still traceback.

## Tests (offline — simulate the agent with fixtures; no live agent)
- Routing: `harness` posture routes `screen`/`extract` to `harness`; `claim_check` stays `anthropic`; `--harness`/`--harness-source` work; forcing a `cloud_only` tool harness raises.
- screen/extract backend=harness, source `claude` AND source `gpt`, NO result: writes `harness/<stage>/<src>/` request + `REQUEST.md`, raises `HarnessWorkPending` (caught → clean message), no final output; the two sources write to DISTINCT dirs (no collision).
- With a FIXTURE result in the source dir: loads via `collect`, writes the normal output, stamps the source, succeeds — idempotent resume.
- Recipe: stops cleanly at a harness step ("awaiting agent (<src>) for step X"); resumes when the fixture result is present.
- Provenance records backend `harness` + the correct SOURCE label; `ai_reporting` does NOT mark harness for elevated review.
- Existing cloud/local routing + claim-check-cloud-only tests still pass.

## Verification before done
- `python -m pytest -q -m "not live"` fully green (no regression to the existing 348).
- `python -m core.tools describe screen` reflects harness eligibility; `EVIDENCE_POSTURE=harness python -m core.tools run screen <run_dir> --vertical .. --harness-source gpt` writes a `harness/screen/gpt/` request + reports the clean awaiting-agent pause.

## Out of scope — do NOT build (this is the FOUNDATION; the next phase is dual-review)
The dual-reviewer ORCHESTRATION (running BOTH sources on the same records as Reviewer 1 + Reviewer 2, reconciling disagreements, computing inter-rater κ, routing conflicts to a human adjudication queue) is the NEXT phase — do NOT build it here. Also out: auto-invoking an agent from the CLI (needs a live session); harness for `risk-of-bias`/`narrative`; web UI; MCP; true background; routing harness through `client.parse`. Note the dual-review next-phase + this backlog in the PR description.

## Implementation order
1. `harness` backend + source dimension + posture + `resolve_backend` (cloud_only protection) + routing tests.
2. `HarnessWorkPending` + screen-tool harness path (source-namespaced request + pending + idempotent resume + source stamping) + tests with fixture results for both sources.
3. extract-tool harness path + tests.
4. CLI + `run_recipe` clean "awaiting agent (<src>)" handling + recipe tests.
5. Provenance (source-aware) + `core/tools/HARNESS.md` model-neutral agent contract.
6. Full suite green; PR description with the dual-review next-phase note + backlog.

## Cross-cutting (validated workflow)
Reuse existing code (`core/sr/agents/*`, `agent_io`); match style; conservative scorecards; offline tests simulate the agent with fixtures. Propose your PLAN first for review before implementing. After you push, Claude/PAI verifies — full suite + cloud_only-never-harness + the per-source request/pending/resume idempotency + source-truthful provenance + the two-source namespacing that the dual-reviewer phase depends on.
