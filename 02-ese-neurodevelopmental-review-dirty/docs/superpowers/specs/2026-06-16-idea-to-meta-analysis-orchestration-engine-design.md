# Idea → Meta-Analysis Orchestration Engine — Design Spec

**Date:** 2026-06-16
**Status:** Draft, hardened by a comprehensive multi-lens review gauntlet (Codex dual-harness + architecture, honesty-gate, reproducibility, security, do-no-harm lenses). All blocker/should findings folded in below; §15 traces them. Ready for user review → implementation plan.

**Goal:** A guided, gated orchestration layer that takes a user from a rough research idea to a full, publishable meta-analysis — chaining the engine's existing stages plus a new front-end and a dual independent-LLM reviewer, with **machine-enforced** honesty gates and an auditable, replayable record.

**Architecture (one line):** A declarative dependency-graph orchestrator drives stage adapters over existing + new functions; a thin conversational "Copilot" driver presents outputs at human gates whose clearance is cryptographically bound to the approved artifact; a provider-agnostic gateway *wraps* the existing LLM client so Reviewer 1 / Reviewer 2 can run on harness, cloud-API, or local models without losing the native structured-output path.

**Tech stack:** Python 3.11, existing onco-evidence-engine modules (`core/sr/*`, `core/llm/client.py`), R (metafor/gemtc/netmeta/multinma) for statistics, LiteLLM (thinly wrapped, hardened) for non-native providers, the Claude↔Codex DualReview harness for the build.

**Intended use (regulatory framing — stamped on every artifact):** This is a research-synthesis productivity tool for trained evidence-synthesis professionals. It is **not a medical device, not clinical decision support, and not for direct patient care.** Every output requires expert human validation before any clinical, guideline, or regulatory use. AI acts as Reviewer 1 (and, in M2+, Reviewer 2); a human is the definitive eligibility and clinical gate.

---

## 1. Why (context & positioning)

The engine already produces honest, end-to-end systematic reviews + meta-analyses (validated in a prior working session; those PFS/OS/κ figures and the competitor teardown are **prior-session context, not design premises** — see the session log / `runs/mm-bcma-early-relapse-refresh/REPORT.md`). Competitive teardown placed our defensible moat at the intersection no competitor occupies: **machine-verifiable trust (cite-or-omit + dual independent-LLM reviewers + sentinel + honesty audit) × time-to-event / hazard-ratio statistics × Bayesian NMA × GRADE × oncology vertical.**

This orchestration engine turns those stages into a *product*. Roadmap: (1) conversational orchestrator → (2) validation against published meta-analyses (the credibility paper) → (3) CLI state-machine + copilot → (4) web wizard. Validation gates productization.

**Guiding intention (non-negotiable):** maximize benefit to patients, physicians, and honest evidence; avoid harm. Encoded as the enforced contract in §7/§7a/§7b — not left to good intentions.

---

## 2. Architecture: three layers

```
DRIVER (thin): "Engine Copilot" skill in Claude Code
  loop: ask orchestrator what's next → run it → show output at a gate → human approve/edit/reject → advance
  (later surfaces: CLI state-machine, web wizard — same backbone)
        │ calls
ORCHESTRATOR (new): core/sr/orchestrator.py
  • STAGES = dependency DAG (declarative)        • run_state.json (status, hashes, gate records)
  • next_stage() · run_stage() · advance() · resume() · auto-mode
  • atomic state writes + run lock + crash recovery
  • enforces honesty gates (§7a) + evidence-strength gate (§7b) + hash-based staleness
        │ invokes via a UNIFORM adapter signature
STAGE ADAPTERS:  fn(run_dir: Path, ctx: RunContext) -> StageResult
  each adapter reads inputs from run_state/run dir, calls the heterogeneous underlying function, writes hashed artifacts
  NEW front-end: idea→PICO · novelty · feasibility · protocol_critic · autoconfig
  EXISTING (wrapped, not "as-is"): search · prescreen · screen(dual) · fulltext · extract · RoB · GRADE · stats/NMA · manuscript
  LLM access via → core/llm/gateway.py  (WRAPS core/llm/client.py; provider-agnostic; harness/api/local)
```

Separation of concerns: **pipeline logic** (adapters) vs **control/state** (orchestrator) vs **conversation** (driver). The driver is replaceable per roadmap surface. The orchestrator subsumes the sequencing currently fused inside `core/sr/pipeline.py:run_sr`/`build_run` (including the sentinel gate and provenance assembly) — see §15 migration note.

---

## 3. The stage model & pipeline

**Stages form a dependency DAG, not a linear list.** A stage is a declarative record:

```python
@dataclass(frozen=True)
class Stage:
    name: str
    fn: Callable            # UNIFORM adapter signature: fn(run_dir, ctx) -> StageResult
    requires: list[str]     # dependency edges (authoritative; execution order = topological sort)
    produces: list[str]     # artifacts written under runs/<id>/
    gate: bool              # pause for human approval after running?
    honesty_gate: bool      # if True, clearance requires a signed human record (§7a); never auto-clearable
    reproducibility: str    # "deterministic" | "stochastic" (§8)
    profiles: set[str]      # filter via the existing Profile.phases (no parallel source of truth)
```

- **Execution order** is the topological sort of `requires` (the table below is a *rendering* of one such order, not the source of truth). "Linear" advancement is removed.
- **Reuse = wrap, not "as-is".** The real functions have incompatible signatures (sync/async, single-item/batch, `Citation`/`dict`/`Corpus`/`Path`). Each gets a thin **adapter** to `fn(run_dir, ctx) -> StageResult`. Highest-risk adapters called out in §15: `meta_analyze` (needs `fig_dir` + reconstructed `modality_by_pmid`), async `gather_corpus`/`resolve_many` (event-loop ownership), single-item `extract` (who owns batching/concurrency).

Pipeline (⛔ = human gate; 🔒 = non-bypassable honesty gate, see §7a):

| # | Stage | New/Wrap | Gate | Milestone |
|---|---|---|---|---|
| 1 | `idea_to_pico` — interview → structured PICO + eligibility | new | ⛔ approve question | M3 |
| 2 | `novelty_check` — search existing SRs/MAs → duplication/gap | new | ⛔ go/no-go | M3 |
| 3 | `feasibility_scope` — scoping search → est. # studies, NMA network connected? | new | ⛔ go/no-go + scope | M3 |
| 4 | `protocol_critic` — knowledge-based pitfall checklist vs draft protocol | new | ⛔ review critique | M3 |
| 5 | `autoconfig` — generate pico/search/extraction YAML → **run dir** (promote step §6) | new | ⛔ approve + promote protocol (PRISMA-S + PROSPERO freeze) | M3 |
| 6 | `search` — multi-source gather + dedup + verify + **frozen corpus snapshot** | wrap | — | M1 |
| 7 | `prescreen` — deterministic pub-type filter + **retraction-status snapshot** | wrap | — (reported) | M1 |
| 8 | `screen_dual` — R1 + R2 (independent models) → agreement + reconcile → conflicts | wrap+new | 🔒 human adjudicates conflicts | M2 |
| 9 | `fulltext` — PMC / Unpaywall / inbox | wrap | — | M1 |
| 10 | `extract` — values + NR/ESTIMATED + quotes | wrap | 🔒 human spot-check sign-off | M1 |
| 11 | `risk_of_bias` — RoB2/ROBINS-I/NOS | wrap | ⛔ (optional) | M1 |
| 12 | `grade` — deterministic certainty (+ mandatory indirectness down-rating for indirect/NMA) | wrap | — | M1 |
| 13 | `stats_nma` — pooled proportions + HRs + NMA + **evidence-strength gate (§7b)** | wrap | ⛔/🔒 review meta-analysis | M1/M4 |
| 14 | `manuscript` — PRISMA+GRADE+forest+prose + honesty audit + cite-or-omit + export re-verification | wrap | 🔒 export gate | M1 |

`protocol_critic` (4) is **knowledge-based, not learned** in v1: a grounded, citable checklist (Cochrane Handbook, PRISMA / PRISMA-S, NICE DSU TSDs, NMA transitivity/consistency, GRADE domains, effect-measure sanity, surrogate-endpoint flags).

The front-end stages (1–5) are **subject to the no-fabrication contract**: any study/PMID/count they emit (e.g. in `novelty_report.json`, `feasibility_report.json`) must be live-verified or flagged unverified before it can inform a ⛔ go/no-go gate.

---

## 4. Reviewer & provider abstraction (LLM-switching) — M2

A `gateway` module mediates LLM calls. It **wraps** the existing `core/llm/client.py` router rather than replacing it: the native Anthropic structured-output path (`messages.parse`, adaptive thinking, effort) that cite-or-omit depends on stays native; LiteLLM is used only for *non-native* providers (the new reviewer roster, local models). One provenance source of truth (the existing `_STAGE_LOG` is unified with the new record, not duplicated).

**Three sources a reviewer/stage can run on:**
- `harness` — Claude Max (`backend: claude`) **or** Codex/ChatGPT (`backend: codex`) → no API key.
- `api` — cloud provider via key.
- `local` — Ollama / vLLM / LM Studio via endpoint URL.

**Reviewer roster** (config; per-vertical or global; overridable per run):
```yaml
reviewers:
  r1:          { source: harness, backend: claude, model: claude-opus-4-8 }   # Max, no key
  r2:          { source: harness, backend: codex,  model: gpt-5.x }           # Codex/ChatGPT, no key
  adjudicator: { source: harness, backend: claude, model: claude-opus-4-8 }   # SHOULD differ from R1; see below
```

**Independence enforcement (gauntlet — honesty/repro/harm converged):**
- `screen_dual` **preflight fails closed** if R1 and R2 resolve to the same model family AND/OR the same endpoint. Provenance records both resolved identities so the independence claim is auditable.
- The agreement metric is recorded and reported as **`llm_llm_agreement`, NOT inter-rater reliability** (two frontier LLMs share training/blind spots; κ between them is not the human-screening reliability statistic methodology expects). A human-involving κ remains the reliability anchor. The adjudicator should be a model distinct from both reviewers.
- `--fallback-single` is **dev-only, structurally non-exportable** (see §7a). The screening honesty gate **fails closed** on fewer than two independent reviewers — it does not vacuously pass.

**Key handling & data egress (security lens):**
- Keys are `SecretStr`-wrapped (raise on `repr`/`str`/serialize); the gateway is the only code that unwraps at HTTP egress. Keys are read from env vars or OS keychain or a gitignored `~/.config/onco-engine/providers.env` whose mode is **stat-checked and refused if broader than 600** or wrong owner. Local models need only a URL.
- `run_state.json`, `provenance.json`, and logs record **provider + model + version only**. A redaction filter is defence-in-depth, *not* the primary control.
- **Local-endpoint validation (SSRF):** default-allow only loopback/private endpoints over https (or loopback http); reject link-local/metadata IPs (169.254.0.0/16, fe80::/10) and, without an explicit `allow_remote_endpoint`, non-loopback hosts; reject cross-host redirects; redact userinfo/query from endpoint strings; record only a sanitized endpoint class in provenance.
- **Data-egress governance:** each provider/source carries a `data_policy` (`retention`, `trains_on_data`, `allowed_content_classes`). Each payload is classified (metadata / abstract / full-text / manuscript); the gateway **fails closed** if a provider's policy doesn't permit that class (e.g., never send paywalled full-text or embargoed manuscript to a non-zero-retention provider). The chosen policy is stamped into provenance. A human gate precedes the first cloud egress of clinical content.
- **LiteLLM hardening:** verbose off, callbacks asserted empty, response cache off (or in-process under the run dir), dependency pinned with hashes, `pip-audit` in CI; treated as a security-reviewed dependency.

---

## 5. Control flow, state & resumability — M1

`runs/<id>/run_state.json` is the source of truth, written **atomically** (`tmp` + `os.replace`) under a **run lock/lease**, carrying a top-level `schema_version` with a `migrate()` path. It stores *pointers and hashes, not payloads*. Each stage records: dependency artifact hashes, output artifact hashes, the params/config/code-commit/prompt-version used, `status` (`pending → running → awaiting_approval → approved → done → failed → stale`), and gate-clearance records (§7a).

Driver loop (identical across surfaces; concurrency model: `run_stage` owns its event loop with a documented "never call from inside a running loop" contract, so async stages — `gather_corpus`, `resolve_many` — and the future web surface compose):
```
status = orchestrator.status(run_dir)
stage  = orchestrator.next_stage(run_dir)     # topo-sort over requires; dirtiness recomputed from hashes
result = orchestrator.run_stage(run_dir, stage)
if stage.gate: driver.present(stage.summarize(result)); human approve/edit/reject (active verification, §7b)
orchestrator.advance(run_dir, stage, signed_decision)
```

- **Staleness = hash-based transitive closure**, computed at `next_stage` and again at export — never trusted from a stored flag. If a downstream artifact's recorded input-hash ≠ the current upstream output-hash, it is stale and blocks export, regardless of any flag. Editing a stage upstream of a 🔒 gate **un-clears that gate** (un-clearing is never auto-clearable).
- **Crash recovery:** resume validates state integrity (schema + checksum); any stage left `running` is re-run; any 🔒 gate without a valid signed-approval record is treated as un-cleared (fail toward re-asking the human).
- **Guided vs `--auto`:** same machine; auto skips ⛔ optional gates but **cannot** clear 🔒 gates (it can only reach `awaiting_approval`) and cannot produce an exportable artifact (§7a).
- **Edit-at-a-gate:** approve / edit / reject; edits recorded as provenance with actor identity and timestamp, and surfaced as protocol deviations (§7b).

`summarize` lives with the driver (a `{stage → summarizer}` map), not on the frozen `Stage` record, keeping presentation out of the pipeline layer.

---

## 6. Data flow & artifacts — M1

Under `runs/<id>/`:
- `run_state.json` — state machine (hashes, gate records). **`runs/` is gitignored by default.**
- `provenance.json` — per-stage model + provider + version, prompt hashes, timestamps, gate decisions/edits with actor identity, agreement metrics, curation drops, data-egress policy. Keys never present.
- `llm_io/` — per-call JSONL: resolved model id (from response), request/response ids, full params, token counts, raw response. The **scrubbed** error sink for failures (§9). This is the replay record and the future training corpus (gated by §11 retention policy).
- `environment.json` — engine git SHA (+ dirty flag), Python + `pip freeze`, R version + invoked package versions (pin via `renv.lock`), OS.
- `search_manifest.json` — per-source exact query, execution timestamp (UTC), DB version if available, **frozen returned PMID/DOI set with retrieval hashes** (reproduction replays this, not a live query) + retraction-status snapshot.
- Front-end outputs (`novelty_report.json`, `feasibility_report.json`, `protocol_critique.json`) and the **generated vertical config**, written to `runs/<id>/generated_vertical/` first; a human **"promote protocol"** action copies it to `verticals/<slug>/` with a freeze hash (the PRISMA-S + PROSPERO pre-registration artifact).

Clean split: `runs/<id>/` = data + state (never committed); `verticals/<slug>/` = reusable, reviewed protocol (committed after promote); `docs/` = specs.

---

## 7. Honesty & safety contract (principles)

- **No fabrication:** cite-or-omit, every PMID live-verified, every value quote-anchored or marked NR/ESTIMATED — extended to the front-end stages (§3).
- **Human-in-the-loop, never bypassed** (enforced in §7a).
- **Transparency over persuasion** (enforced in §7b).
- **AI = Reviewer 1 (and 2 in M2+); human is the definitive eligibility/clinical gate.**
- **ML may augment, never silently set a published result** (§11).

## 7a. Enforcement (machine-verifiable — the core gauntlet fix)

§7 is enforced by mechanism, not prose:
- **Signed, content-bound gate clearance.** Each 🔒 gate (screen adjudication, extraction sign-off, manuscript export) clears only with a record that binds a **human actor identity + timestamp + the SHA-256 of the exact artifact bytes approved**. The record is append-only/monotonic.
- **Human ≠ AI.** Clearing a 🔒 gate requires an out-of-band human assertion the orchestrator cannot self-generate (interactive confirmation / separate `approve` invocation stamping identity). `--auto` is structurally incapable of producing it — it stops at `awaiting_approval`.
- **Export re-verifies independently.** The manuscript/export stage recomputes artifact hashes and refuses unless all three gate records exist, match current bytes, and no required stage is hash-stale. A flipped JSON flag without a matching signature-over-content fails closed.
- **`--auto` produces no exportable artifact.** It may write only a structurally-degraded draft to a quarantined `runs/<id>/draft_UNVALIDATED/` path (numeric results redacted/placeholder, machine-detectable `validation_status: uncleared` in every format, unremovable footer/metadata). The cite-or-omit export path is simply not generated.
- **`--fallback-single` is dev-only and permanently non-exportable** (quarantine only); the screening gate fails closed on < 2 independent reviewers.
- **Honesty audit is a precondition, not an internal step:** "audit passed" is a separately-recorded, export-checked record; if the audit errors/skips, export refuses.

## 7b. Evidence-strength / interpretability gate (do-no-harm — non-bypassable)

A coded gate before any headline estimate or export; each trigger binds a non-removable caveat and requires explicit per-flag human acknowledgment (recorded):
- **Heterogeneity / sparsity:** I² above a pre-registered threshold (default >75%) or k below a floor (default <5 proportions, <3 HR) → **suppress the headline point estimate**, force display of the **prediction interval**, auto-downgrade GRADE imprecision.
- **Single-arm proportions** → labeled `NON-COMPARATIVE — descriptive only`, physically firewalled from comparative claims; a deterministic prose linter blocks language juxtaposing two single-arm pools as comparative.
- **Indirect / NMA evidence** → mandatory **GRADE indirectness down-rating** propagated to the manuscript; transitivity assessment + network-connectedness surfaced as a signed gate; a disconnected/thin network **blocks** the comparative claim (not a footnote); node-split/consistency results reported.
- **Surrogate endpoints** → every outcome tagged `definitive (OS, QoL)` vs `surrogate (PFS, ORR, MRD, DoR)`; surrogate results carry a non-removable "not OS-validated in this setting unless cited" caveat; the conclusion linter forbids surrogate-as-clinical-benefit phrasing.
- **Conclusion calibration:** conclusion strength must match GRADE certainty; comparative/superiority language banned unless backed by a direct comparative estimate of adequate certainty.
- **Anti-automation-bias:** extraction sign-off requires viewing the source quote/span for a randomized non-skippable sample and confirming values; screening adjudication requires a typed rationale per conflict; export presents a one-screen "honesty ledger" (k, I², prediction intervals, # NR/ESTIMATED, # curation drops, surrogate/indirect flags, RoB distribution) requiring per-flag acknowledgment. Interaction/dwell recorded.
- **Disclosure & integrity:** a non-removable **AI-contribution statement** (which stages were AI-performed, with models/versions, vs human-verified) generated *from* the gate-interaction record so it cannot overstate human involvement; **protocol-freeze + deviation log** (post-freeze eligibility/outcome edits appear in a mandatory "deviations from protocol" section); **COI/funding capture** for included trials and the review itself; an applicability/representativeness (equity) caveat where the evidence base under-represents the target population.

---

## 8. Reproducibility & validity

- **Reproducibility is tiered, not "exact".** Each stage is tagged `deterministic` (R stats, κ math, dedup, GRADE — bit-reproducible) or `stochastic` (every LLM stage — *replayable/auditable*, never bit-reproducible due to provider drift). Claims are scoped accordingly; the deterministic test (§12) asserts the boundary.
- **What's recorded to make stochastic stages replayable:** prompt template hashes + versions, full raw LLM I/O (`llm_io/`), resolved model id from the response, request params (temperature/top_p/seed where honored, with "not honored" flags), engine git SHA, package/R versions, search snapshot, retraction snapshot (all §6).
- **Validation/benchmark mode (designed-for; built in M4 — neutralizes the "graded their own homework" attack):** a `benchmark` run mode that (1) freezes a **pre-registered protocol hash** committed *before* search and refuses to start without it; (2) keeps the published gold standard in a **sealed store the orchestrator cannot read during the run**, opened only by a separate no-LLM `benchmark_compare` script after gates clear; (3) supports `--blind` prompts (no target-answer hints); (4) reports **pre-specified** comparison metrics (included-study precision/recall/F1 vs the published set, pooled-estimate ratio + CI overlap, was the published point inside our CI) declared in the pre-registered protocol; (5) records distinct actor identities for reviewers, adjudicator, and human.
- **Statistical-validity guards:** the §7b evidence-strength gate; analysis-method registry entries (§11) declare applicability preconditions (effect measure, min studies, network-connectedness, transitivity) validated against the actual data with recorded warning/fail-closed.
- **Living reviews:** each cycle writes an immutable timestamped `runs/<id>/snapshots/<utc>/` (frozen search manifest, retraction snapshot, environment, included-set diff vs prior, prior estimate, parent-snapshot pointer); a re-run targets a named snapshot, never live data.

---

## 9. Error handling

- **Stage failure** → `status=failed`, error **scrubbed** before persistence (`scrub_exception()` is the exclusive sink: typed code + provider + model + HTTP status only — never raw message/cause/payload, which can carry keys + clinical text); orchestrator stops (never silent-skip); `resume` re-runs that stage. One bad outcome doesn't sink the run (existing per-outcome guards in stats).
- **Fail-closed honesty paths** — missing reviewer key/source, non-independent reviewers, broken citation, unverified PMID, missing data-egress policy, SSRF-invalid endpoint → stop with a clear (scrubbed) message.
- **Schema-invalid LLM output** → existing collect/repair: re-run invalid items, never ship partial invalid data.
- **`--auto` on failure** → pauses and surfaces; never emits a "complete" dossier from a failed stage; never an exportable artifact (§7a).
- **No raw exception or request object is ever written to disk, git, or the conversation transcript** — an honesty-contract invariant with a dedicated test (§12).

---

## 10. Security (summary; details in §4/§6/§9)

`SecretStr` keys; gitignored `runs/` + `~/.config/onco-engine/`; stat-enforced 600 perms; SSRF-validated local endpoints; per-provider data-egress governance with content-class fail-closed; `scrub_exception` so secrets/clinical text never persist; LiteLLM hardened (no verbose/callbacks/cache) and hash-pinned with `pip-audit`; pre-commit secret scan (gitleaks/detect-secrets); `summarize` is the scrubbed chokepoint for anything shown in the transcript; a `scrub` command strips provenance to model-id-only before any export/commit.

---

## 11. ML / extensibility — now vs future

**v1 (M1–M4) — deterministic & knowledge-based + sockets for ML:**
- **Pluggable analysis-method registry** in `stats_nma` (M4): each entry declares applicability preconditions, validated against the data (warning/fail-closed); methods drop in without touching the rest.
- **`protocol_critic`** (M3) — knowledge-based, no ML.
- **Extension points only:** `provenance.json` + `llm_io/` are structured to become the training corpus. (The earlier "gateway absorbs ML models like LLMs" claim is dropped — an LLM call (text→schema) and a statistical/ML model (matrix→estimate) need different interfaces; the ML interface is a separate future design.)

**Future phase (designed-for, NOT built — gated on the validation corpus):** learned/predictive layer — ML meta-regression / effect-modifier discovery; a learned pitfall detector (upgrades the knowledge-based critic); an oncology fine-tuned model (could serve as R2); preference-learning from gate decisions; embeddings-based trial-dedup & comparator-label mapping; anomaly/outlier detection. All in the assist/augment role; the validation paper produces the corpus.

---

## 12. Testing

- **Orchestrator** — stub-stage unit tests (uniform `fn(run_dir, ctx)` makes a shared harness possible): topo-ordering, dependency resolution, hash-based staleness transitive closure, gate pausing, resume-from-each-state incl. crash-during-`running`, atomic-write + lock, schema migration, every fail-closed path.
- **Enforcement (§7a)** — `--auto` cannot clear 🔒 gates or produce an exportable artifact; a hand-edited `approved` flag without a content-matching signature fails export; `--fallback-single` is non-exportable; export re-verifies all three gate records against current hashes; edit-upstream un-clears a downstream 🔒 gate.
- **Evidence-strength (§7b)** — seeded high-I²/low-k suppresses headline + forces prediction interval; single-arm firewall; indirect down-rating; surrogate caveat; conclusion linter blocks over-claims.
- **Gateway/security** — reviewer resolution across harness/api/local; preflight refuses non-independent R1/R2; keys never in state/logs/transcript (redaction + `scrub_exception` corpus test across all provider key formats); SSRF rejection of metadata/link-local/remote endpoints; data-egress fail-closed by content class.
- **Reproducibility** — deterministic stages bit-reproduce on re-run with pinned env; stochastic stages produce a complete replay record; benchmark mode refuses to start without a pre-registered protocol hash and cannot read the sealed gold standard mid-run.
- Full suite green before each merge.

---

## 13. Build methodology

Implemented via the Claude↔Codex **DualReview harness** — every work package gated on a Codex `VERDICT: APPROVE` (the process that produced the WP1–WP7 hardening and this very spec review). Targeted `git add`, full pytest green before merge, co-author trailer.

---

## 14. Milestones (phased; each ships + is dual-harness reviewed)

- **M1 — Trust backbone.** Orchestrator DAG + uniform stage adapters over the **existing** stages (search→manuscript); `run_state.json` with hashes, atomic writes, run lock, crash recovery; **machine-enforced honesty gates (§7a)**; reproducibility provenance (`provenance.json`, `llm_io/`, `environment.json`, `search_manifest.json`); migrate sequencing out of `run_sr`/`build_run`. *No new front-end, no dual-LLM yet.*
- **M2 — Provider gateway + dual independent-LLM reviewers.** `gateway` wrapping `client.py`; reviewer roster (harness/api/local); independence preflight; `llm_llm_agreement` labeling + human-anchored κ; full security hardening (SecretStr, SSRF, egress governance, scrub_exception, LiteLLM hardening).
- **M3 — Front-end.** `idea_to_pico`, `novelty_check`, `feasibility_scope`, `protocol_critic` (knowledge-based), `autoconfig` → generated config → promote; PROSPERO freeze + deviation log; front-end no-fabrication enforcement.
- **M4 — Statistics & validation.** Analysis-method registry with applicability preconditions; the full **evidence-strength gate (§7b)** wired into `stats_nma`/`manuscript`; **benchmark/validation mode (§8)** for the credibility paper.

Out of all milestones (future sub-projects): CLI state-machine surface; web wizard; the learned/predictive ML layer; the validation study + paper itself (its own spec, runs on M4).

---

## 15. Resolved gauntlet findings & migration notes

**Migration (architecture lens):** `core/sr/pipeline.py:run_sr` is today's de-facto orchestrator and `build_run` owns the sentinel gate + provenance assembly. M1 moves sequencing into `orchestrator.py`; the sentinel and provenance move into the orchestrator/`advance()` (honesty gates belong in the control layer). The new `gateway` **wraps** `client.py` (keeps native `parse`/adaptive-thinking for cite-or-omit); one provenance source of truth.

**Folded-in blockers (trace):** enforcement gap → §7a; `--fallback-single`/single-reviewer export → §4, §7a; R1≠R2 independence + κ mislabel → §4, §8; "exact reproduction" overclaim → §8; evidence-strength/do-no-harm (heterogeneity, single-arm, indirect, surrogate) → §7b; security (exception scrub, gitignore, SSRF, egress, SecretStr) → §4, §9, §10; state robustness (hashes, atomic, lock, recovery) → §5; DAG staleness + uniform adapters → §3, §5; generated-config promote → §6; validation rigor (blind/pre-register/sealed gold) → §8; scope split → §14; intended-use clause → header/§1; claims marked prior-session context → §1.

**Remaining open questions for implementation planning:**
- Exact I²/k thresholds and surrogate taxonomy per outcome family (clinical input; default values stated in §7b).
- Signed-approval mechanism concretely (OS keychain signature vs a simple HMAC over content with a local key) — decide in M1 planning.
- Whether `adjudicator` must be a third distinct family or may equal R1 with a logged caveat — decide in M2 planning.
