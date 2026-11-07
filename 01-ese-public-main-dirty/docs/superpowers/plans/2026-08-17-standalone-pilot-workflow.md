# Standalone Pilot Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ESE easy for independent reviewers to install, configure, run on their own protocols, and return auditable validation evidence without Claude Science, Elicit, or a separate coordinator.

**Architecture:** Keep the existing ESE stage DAG and run artifacts as the execution core. Add a thin pilot control plane consisting of preflight diagnostics, protocol locking, guided run-until checkpoints, draft-only export, and a validation/packaging layer. The first pilot surface is CLI plus Markdown/JSON artifacts; no web application is introduced until pilot evidence shows that CLI usability is the limiting failure.

**Tech Stack:** Python 3.10+, existing `ese` console entry point, Pydantic, YAML, JSON, pytest, existing PubMed/Europe PMC/ClinicalTrials.gov connectors, existing R statistics bridge, local or Anthropic model provider.

**Spec:** `docs/superpowers/specs/2026-06-16-idea-to-meta-analysis-orchestration-engine-design.md`, narrowed here to the standalone pilot control plane and validation harness.

## Global Constraints

- Preserve the current run-state DAG, artifact hashes, HMAC-signed honesty gates, and fail-closed export behavior.
- AI output remains Reviewer-1 draft material; a pilot run must never silently present conclusions as clinically validated.
- A local-only pilot must explicitly identify itself as single-AI-reviewer plus human review unless two independent model families/endpoints are configured.
- Never persist API keys, credentials, institutional-license material, or paywalled full text in validation bundles by default.
- Do not modify or merge the independent benchmark runs; standalone validation consumes copied artifacts.
- Every new command must support a human-readable default and a machine-readable `--json` mode.
- Every feature task ends with offline tests before any live-network pilot.

---

### Task 1: Define the pilot contract and protocol package

**Files:**
- Create: `docs/standalone-pilot/README.md`
- Create: `docs/standalone-pilot/protocol-template.yaml`
- Create: `docs/standalone-pilot/reviewer-runbook.md`
- Create: `core/sr/protocol/lock.py`
- Create: `tests/test_protocol_lock.py`

**Interfaces:**
- `freeze_protocol(source: Path, run_dir: Path) -> Path` copies the validated protocol into `run_dir/protocol.yaml`, writes `run_dir/protocol_lock.json`, and records SHA-256 hashes for protocol, vertical, and extraction schema.
- `load_protocol_lock(run_dir: Path) -> dict` raises `ProtocolLockError` when the lock is missing, malformed, or no longer matches the protocol bytes.
- `protocol-template.yaml` contains review identity, PICO/PCC, eligibility, search cutoff, sources, outcomes, review mode, model data policy, and full-text policy.

- [ ] Write failing tests for a valid protocol lock, changed protocol bytes, missing lock, and invalid required fields.
- [ ] Run `pytest tests/test_protocol_lock.py -q`; confirm the new tests fail before implementation.
- [ ] Implement validation and atomic lock writing with the existing hashing utilities.
- [ ] Add examples for the three explicit modes: `pilot` (one AI reviewer plus human gates), `governed` (independent reviewers plus human adjudication), and `offline-replay` (stored artifacts only).
- [ ] Run `pytest tests/test_protocol_lock.py -q` and confirm all tests pass.
- [ ] Document a tester’s first five minutes: copy the template, fill protocol fields, run `ese doctor`, then initialize a run.
- [ ] Commit as `feat: add standalone protocol package contract`.

### Task 2: Add a safe environment and connector preflight

**Files:**
- Create: `core/sr/driver/doctor.py`
- Modify: `core/sr/driver/cli.py`
- Test: `tests/test_ese_doctor.py`
- Modify: `docs/standalone-pilot/README.md`

**Interfaces:**
- `DoctorCheck(name: str, status: str, detail: str, remediation: str = "")`.
- `DoctorReport(checks: list[DoctorCheck], overall: str)` with `to_dict()` and `render_text()` methods.
- `run_doctor(cfg, *, run_dir: Path | None = None, require_llm: bool = True) -> DoctorReport`.
- CLI: `ese doctor [--vertical NAME] [--run RUN_DIR] [--json] [--no-llm]`.

Checks must cover:

1. Python version and installed package compatibility.
2. Rscript availability and `metafor`/`netmeta` availability when the selected profile needs statistics.
3. Model mode, resolved provider/model identity, and missing-key status without printing key material.
4. PubMed, Europe PMC, and ClinicalTrials.gov read-only reachability.
5. Full-text inbox and run-directory writability.
6. Protocol lock presence and hash validity when `--run` is supplied.
7. Explicit warning when the configured mode claims dual review but the reviewer roster is not independent.

- [ ] Write tests for passing checks, missing API key, unavailable R, invalid permissions, connector failure, and JSON output.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement checks using existing connector/config/security functions; do not duplicate credential parsing.
- [ ] Add exit codes: `0` all required checks pass, `1` required check failed, `2` warning-only pilot state.
- [ ] Run `pytest tests/test_ese_doctor.py -q`.
- [ ] Run `./.venv/bin/ese doctor --vertical systematic-review --no-llm --json` and verify no secrets appear.
- [ ] Commit as `feat: add standalone preflight diagnostics`.

### Task 3: Create the guided standalone runner

**Files:**
- Create: `core/sr/driver/runbook.py`
- Modify: `core/sr/driver/cli.py`
- Modify: `core/sr/driver/status.py`
- Test: `tests/test_ese_runbook.py`
- Modify: `docs/standalone-pilot/reviewer-runbook.md`

**Interfaces:**
- `RUN_TARGETS = ("search", "prescreen", "screen", "fulltext", "extract", "stats", "draft")`.
- `run_until(run_dir: Path, target: str, *, cfg, retmax: int | None = None) -> RunbookResult`.
- `RunbookResult(status: str, completed: list[str], blocked_stage: str | None, next_command: str | None, summary: dict)`.
- CLI: `ese run RUN_DIR --until TARGET [--retmax N] [--json]`.

Behavior:

- Run every ancestor stage until the target or the next honesty gate.
- On a gate, return exit code `3`, print exactly what the reviewer must inspect, and show the exact approval command.
- On a failed stage, return exit code `1` with the existing scrubbed error code and remediation hint.
- On completion, print artifact paths, protocol hash, model provenance, and the next recommended command.
- `draft` runs through narrative/manuscript preparation but never creates a conclusion sign-off or clears the final export gate.
- Re-running the same command must resume from the run state rather than repeat completed stages.

- [ ] Write tests for ancestor execution, gate pause, failed-stage retry, resume, JSON output, and invalid targets.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the runner as a thin wrapper around `engine.next_stage`, `engine.run_stage`, and existing run locks.
- [ ] Add a compact status view with four labels: `READY`, `RUNNING`, `NEEDS HUMAN REVIEW`, and `BLOCKED`.
- [ ] Run `pytest tests/test_ese_runbook.py tests/test_ese_driver.py -q`.
- [ ] Execute an offline fixture run through `--until stats` and verify deterministic artifacts are reproduced on a second run.
- [ ] Commit as `feat: add guided standalone runbook`.

### Task 4: Add draft export and portable run bundles

**Files:**
- Create: `core/sr/driver/bundle.py`
- Modify: `core/sr/driver/cli.py`
- Modify: `core/sr/manuscript/export.py`
- Test: `tests/test_ese_draft_bundle.py`
- Modify: `.gitignore`
- Modify: `docs/standalone-pilot/reviewer-runbook.md`

**Interfaces:**
- CLI: `ese draft RUN_DIR [--format md|html] [--out PATH]`.
- CLI: `ese bundle RUN_DIR --out PATH [--include-fulltext] [--json]`.
- `build_bundle(run_dir: Path, destination: Path, *, include_fulltext: bool = False) -> Path`.
- `bundle_manifest(run_dir: Path, *, include_fulltext: bool) -> dict`.

Draft export must:

- Carry a visible `DRAFT — awaiting human review` banner.
- Include protocol lock, search manifest, PMID verification, extraction audit, NR/ESTIMATED counts, statistics, evidence-strength warnings, and unresolved items.
- Refuse to emit treatment recommendations or final conclusions.
- Leave signed gate records and conclusion sign-off untouched.

Bundles must include protocol/config hashes, run state, provenance, environment, stage summaries, gate records, audit files, and report outputs. They must exclude secrets, `.env`, gate keys, and full text unless `--include-fulltext` is explicitly supplied. A manifest must list every included and excluded path.

- [ ] Write tests proving draft output remains locked without Reviewer-2 sign-off.
- [ ] Write tests proving bundles exclude secrets and include hashes/manifests.
- [ ] Run focused tests and confirm failure.
- [ ] Implement draft rendering by reusing the existing manuscript renderer with an explicit draft context.
- [ ] Implement bundle creation with deterministic file ordering and SHA-256 entries.
- [ ] Run `pytest tests/test_ese_draft_bundle.py tests/test_sr_export_gate.py -q`.
- [ ] Open a generated Markdown draft and manifest manually before commit.
- [ ] Commit as `feat: add draft reports and portable run bundles`.

### Task 5: Build the cross-protocol validation harness

**Files:**
- Create: `validation/README.md`
- Create: `validation/cases/README.md`
- Create: `validation/schema/pilot_case.yaml`
- Create: `validation/score.py`
- Create: `validation/report.py`
- Create: `tests/test_validation_harness.py`
- Modify: `pyproject.toml` only if a package entry point is needed

**Interfaces:**
- `load_case(path: Path) -> PilotCase`.
- `score_run(run_dir: Path, case: PilotCase) -> dict`.
- `render_validation_report(scores: list[dict], out: Path) -> Path`.
- CLI: `python -m validation.score RUN_DIR --case CASE.yaml --json`.

Each pilot case records:

- Protocol class: proportion, comparative binary, time-to-event, or NMA-capable.
- Expected source cutoff and allowed databases.
- A sealed human-adjudicated sample of 20–30 records, not exposed to ESE before the run.
- Gold screening labels, duplicate-population decisions, and extraction values for the sample.
- Required outcomes and whether estimates are allowed.

The harness must report:

1. Setup success and time to first valid run.
2. Stage completion and maintainer interventions.
3. Gate adherence and blocked unsafe actions.
4. PMID/DOI/NCT verification rate.
5. Eligibility precision/recall on the adjudicated sample.
6. Duplicate-population agreement.
7. Extraction value agreement, quote coverage, NR accuracy, and ESTIMATED-label accuracy.
8. Deterministic-statistics reproducibility from the portable bundle.
9. Run duration, API/local-model mode, and failure category.

Initial development thresholds, not clinical-validity claims:

- 100% of offline safety/gate tests pass.
- 0 gate bypasses or secret leaks.
- ≥95% source-identifier verification on the audited sample.
- ≥90% eligibility agreement on the 20–30 record adjudicated sample.
- ≥95% quote presence for non-NR extracted values.
- Byte-identical deterministic statistics when replayed from the same bundle.

- [ ] Write failing tests for case parsing, score calculation, missing gold labels, and deterministic report generation.
- [ ] Implement code-based scoring first; do not use an LLM judge for primary acceptance metrics.
- [ ] Add a human-review worksheet format for disagreements and failure taxonomy.
- [ ] Run the harness against existing offline fixtures and verify stable JSON output.
- [ ] Add a small capability repeat test: three runs of one public protocol, reporting pass@3 and pass^3 for the defined acceptance criteria.
- [ ] Commit as `feat: add cross-protocol validation harness`.

### Task 6: Run the handoff pilot and convert failures into regression tests

**Files:**
- Create: `docs/standalone-pilot/pilot-plan.md`
- Create: `validation/pilot-log-template.yaml`
- Create: `validation/failure-taxonomy.yaml`
- Create: `tests/fixtures/validation/`
- Modify: `docs/standalone-pilot/README.md`

Pilot design:

- Recruit 3–5 reviewers with different protocol types and evidence-synthesis experience.
- Give each reviewer the same versioned installer, protocol template, runbook, and `ese doctor` command.
- Each reviewer runs one protocol independently, with no maintainer operating the CLI for them.
- Collect the portable bundle, validation report, time log, support questions, and one 20–30 record adjudicated sample.
- Repeat one representative protocol three times to measure stochastic consistency.
- Keep personal data and institution-specific credentials out of the shared validation repository.

Pilot exit criteria:

- Every reviewer can reach a draft report without maintainer intervention or the blocker is classified.
- Every failure is assigned to one taxonomy category: installation, protocol configuration, connector, model/runtime, full text, screening, extraction, statistics, gate, export, or usability.
- At least 20 real failures or friction events are captured before redesigning the workflow.
- The top three failure categories each have a regression test or a documented reason not to automate them.
- The validation report states what passed, what failed, and what remains unverified; it does not call ESE validated merely because a report was produced.

- [ ] Run the offline fixture suite before distributing the pilot package.
- [ ] Give each pilot reviewer the same version and record the environment fingerprint.
- [ ] Collect bundles and adjudication worksheets without collecting credentials.
- [ ] Score all cases with `validation.score`.
- [ ] Convert recurring failures into deterministic tests before changing product behavior.
- [ ] Publish a pilot memo with go/no-go decisions for the next iteration.
- [ ] Commit as `docs: add standalone pilot handoff and validation protocol`.

## Recommended implementation order

Implement Tasks 1–4 first. That produces a usable standalone pilot before the full validation harness exists. Implement Task 5 against the first pilot bundle, then run Task 6. Do not start a web UI until the pilot logs show that the CLI/runbook—not evidence quality, model behavior, full-text availability, or protocol ambiguity—is the dominant failure source.

## Self-review

- Protocol setup, execution, gates, draft output, packaging, and validation each have a separate deliverable and test cycle.
- The plan preserves the existing ESE trust model rather than weakening it for usability.
- The plan distinguishes single-AI plus human pilot mode from independent dual-reviewer mode.
- The pilot measures both capability and consistency, including pass@3 and pass^3 on a small repeat set.
- No clinical-validity claim is inferred from successful software execution.
