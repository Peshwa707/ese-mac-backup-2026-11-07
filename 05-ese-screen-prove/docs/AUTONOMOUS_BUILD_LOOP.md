# Autonomous build loop

## Objective

Continue building the ESE engine in this isolated repository, one tested
engine package at a time, until the engine is ready for an explicit
user-approved live-API qualification run. The WebUI is an independent lane and
must consume versioned engine artifacts/contracts; this engine loop must not
edit or drive the WebUI repository. Full-stack integration is a later offline
compatibility gate, not a reason to mix the two implementations.

## Roles

- Claude/PAI: selects one bounded work package, writes tests first, implements,
  stages the diff, and requests review.
- Codex worker: independently reviews the staged diff and returns
  `VERDICT: APPROVE` or `VERDICT: CHANGES`.
- User: resolves disputed findings and authorizes the transition to live APIs.

## Stage contract

The product goal is: **produce a reproducible, offline-first ESE engine that
can take a frozen review input through each governed evidence stage, preserve
uncertainty and provenance, and expose versioned artifacts that an independent
WebUI can drive.** Live connectors and model calls are qualification work after
the offline engine is ready, not hidden prerequisites for building it.

Every stage must define and record four things before implementation:

1. **Goal** — one observable capability the stage adds.
2. **Deliverable** — the smallest code, schema, and fixture set that provides it.
3. **Verification** — the exact offline test or replay command that must pass.
4. **Exit condition** — tests pass, artifacts are deterministic, scope is
   bounded, and the independent reviewer returns `VERDICT: APPROVE`.

The implementer may work only on the first incomplete stage, then re-evaluate
the queue after the approved commit. Each approved stage must leave a durable
receipt or test evidence under `docs/reviews/`.

## Work-package queue

| Stage | Goal | Deliverable | Verification / exit |
|---|---|---|---|
| 1. Doctor | Detect whether this checkout is ready for offline work. | Readiness command covering install, CLI smoke, tests, cleanliness, remotes, and project credentials. | `pytest -q` plus doctor tests; reviewer approval. |
| 2. Boundaries | Make every future live provider/connector opt-in and explicit. | Provider/connector contracts and exact opt-in gate; no live implementation. | Boundary tests prove default denial and offline request description; reviewer approval. |
| 3. Public canaries | Specify bounded PubMed, ClinicalTrials.gov, and one LLM canary without executing them. | Versioned request schemas, capability/access states, identity checks, and task-envelope fields. | Fixture-only schema/replay tests; no network; reviewer approval. |
| 4. Frozen extraction | Convert frozen full-text/reference inputs into auditable extraction records. | Extraction schema, source spans, `NR`/`ESTIMATED` handling, and replayable fixtures. | Byte-stable replay and tamper tests; reviewer approval. |
| 5. Risk of bias | Preserve domain-level judgments without silently inventing missing data. | Bounded RoB schema, rationale/source links, abstention/unknown states. | Fixture validation and deterministic replay; reviewer approval. |
| 6. Synthesis calculations | Calculate only protocol-supplied, reproducible quantities. | Analysis recipe, effect-measure validation, calculation artifacts, and uncertainty flags. | Golden numerical fixtures and altered-input failure tests; reviewer approval. |
| 7. Offline qualification | Demonstrate the engine works end-to-end without network access. | Qualification receipt covering all completed stages and artifact hashes. | Full test suite, offline replay, tamper checks, and reviewer approval. |
| 8. Engine validation matrix | Exercise every completed engine component across normal, missing, ambiguous, malformed, boundary, and tamper cases without relying on model consensus. | Versioned fixture matrix, explicit expected outcomes, adversarial/negative tests, and a validation receipt covering protocol, import/deduplication, screening, extraction, risk of bias, synthesis, replay, and run-state gates. | Full matrix replay, deterministic output, negative-test coverage, and reviewer approval. |
| 9. Engine/WebUI contract | Let the independent WebUI drive the engine using versioned artifacts. | Compatibility fixture and documented engine API/artifact contract. | Offline compatibility smoke test only; reviewer approval. |
| 10. Current CLI contract | Bring the run-level extraction, risk-of-bias, and synthesis CLI surfaces into the documented current contract and qualification checks. | Versioned CLI fixtures, command-shape documentation, and current-head contract/qualification evidence. | Full suite, CLI fixture tests, contract receipt, and reviewer approval. |
| 11. Engine offline completion | Prove the current engine checkout is ready for the separate WebUI full-stack gate. | `docs/reviews/ENGINE_OFFLINE_RECEIPT.md` with `ENGINE_OFFLINE_VERIFIED: true`, current commit/tree, test and replay evidence, and explicit live-API exclusion. | Current-head full suite, qualification, validation, contract, CLI smoke, and reviewer approval. |

No later item may be started if an earlier item has an unresolved review finding.

Cross-cutting engine input: `docs/CLAUDE_SCIENCE_WORKFLOW_LEARNINGS.md` records
observed workflow patterns that must become versioned contracts or offline
tests before they are exposed through the independent WebUI or live connectors.

## Hard stop before live APIs

The autonomous loop must stop after item 11. It must not:

- enable network access;
- read or configure API credentials;
- run PubMed, ClinicalTrials.gov, LLM, institutional, or subscription calls;
- purchase access or accept license terms;
- modify the current ESE repository;
- merge or pool evidence;
- claim scientific validation.

Live testing begins only after the user explicitly authorizes it in a later step.

## Review and recovery rules

- One work package per handoff.
- Tests must pass before staging.
- Every staged diff requires an independent Codex verdict.
- Re-review after every change.
- Continue re-reviewing the same bounded package until it receives
  `VERDICT: APPROVE`; the round count is recorded for auditability but does
  not automatically interrupt the loop.
- Never commit on an unapproved verdict.
- Preserve all handoffs, verdicts, and readiness receipts under `docs/reviews/`.
