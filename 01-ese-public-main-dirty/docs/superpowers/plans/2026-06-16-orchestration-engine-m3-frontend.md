# Orchestration Engine — M3 (Front-End: idea → protocol) Plan

> **For agentic workers:** Executed via the Claude↔Codex DualReview harness. Each WP gated on Codex `VERDICT: APPROVE` + full suite green. TDD within each WP.

**Goal:** Add the "idea → registerable protocol" front-end that the pipeline currently lacks: turn a rough research idea into a structured PICO, check novelty, scope feasibility, critique the draft against known pitfalls, and generate a reusable vertical config — with front-end no-fabrication enforcement and a PROSPERO-style protocol freeze.

**Architecture:** Five new stage adapters (uniform `fn(run_dir, ctx) -> StageResult`) producing JSON artifacts under the run dir; LLM stages route through the M2 gateway (content_class="metadata", Reviewer-1 spec); `protocol_critic` is knowledge-based (no ML). `autoconfig` writes a generated vertical under the run dir; a human "promote" copies it to `verticals/` with a freeze hash (the PROSPERO pre-registration artifact). Every study/PMID a front-end stage emits is live-verified or flagged unverified.

**Tech stack:** Python 3.11, M2 gateway + reviewers, existing connectors (PubMed/EuropePMC) for novelty/feasibility verification, pydantic schemas, pytest.

**Spec:** §3 (stages 1-5), §7 (no-fabrication extended to front-end), §7b (PROSPERO freeze + deviation log).

---

## WP-M3.1 — `idea_to_pico`
**Files:** Create `core/sr/orchestrator/frontend.py` (+ `PicoDraft` schema), `tests/orchestrator/test_frontend_pico.py`.
- Read `idea.txt` (rough idea) → gateway_parse(`PicoDraft`, …, spec=resolve_reviewer(cfg,"r1"), content_class="metadata") → `pico.json`. `PicoDraft`: population/intervention/comparator/outcomes + inclusion_criteria[] + exclusion_criteria[] + framing (PICO|PCC).
- Gate (⛔ approve question). Acceptance: stub gateway → pico.json written with the structured fields + input hash recorded; missing idea.txt fails closed.

## WP-M3.2 — `novelty_check`
**Files:** add to `frontend.py`, `tests/orchestrator/test_frontend_novelty.py`.
- Search existing SRs/MAs (PubMed filter: systematic review/meta-analysis) for the PICO terms → `novelty_report.json` (prior reviews with **live-verified** PMIDs, duplication assessment, gap statement). Front-end no-fabrication: drop/flag any PMID that does not verify.
- Gate (⛔ go/no-go). Acceptance: stub search+gateway → report lists only verified PMIDs; an unverified PMID is flagged/omitted (not asserted as real).

## WP-M3.3 — `feasibility_scope`
**Files:** add to `frontend.py`, `tests/orchestrator/test_frontend_feasibility.py`.
- Scoping search → estimate # primary studies + whether a connected comparator network exists (NMA viability) → `feasibility_report.json`.
- Gate (⛔ go/no-go + scope). Acceptance: stub → report has study-count estimate + nma_connected bool + honest "estimate" labeling.

## WP-M3.4 — `protocol_critic` (knowledge-based)
**Files:** Create `core/sr/orchestrator/protocol_critic.py` (+ a citable rule set), `tests/orchestrator/test_protocol_critic.py`.
- Check the draft PICO/plan against a grounded pitfall checklist (PRISMA/PRISMA-S, NICE-DSU transitivity/consistency, GRADE domains, effect-measure sanity, surrogate-endpoint flags) → `protocol_critique.json` (findings with rule id + citation + severity). **No ML.**
- Gate (⛔ review critique). Acceptance: seeded weak protocol (vague comparator, surrogate-only outcome, NMA without transitivity note) triggers the expected rule ids; a clean protocol yields no high-severity findings.

## WP-M3.5 — `autoconfig` + promote + PROSPERO freeze
**Files:** add to `frontend.py`, `tests/orchestrator/test_frontend_autoconfig.py`.
- Generate `generated_vertical/{pico,search_strategies,extraction_schema}.yaml` from the approved PICO (validated against the existing schema validator) under the run dir.
- `promote_protocol(run_dir, slug)` (human action) copies to `verticals/<slug>/` and writes a `protocol_freeze.json` (content hash + UTC ts) = the PROSPERO pre-registration artifact; post-freeze edits are recorded as deviations.
- Gate (⛔ approve + promote). Acceptance: generated YAML validates; promote writes the vertical + a freeze hash; a post-freeze PICO edit is recorded as a deviation.

---

## Self-review
- Spec stages 1-5 → WP3.1-3.5; no-fabrication (front-end) → WP3.2 (verified PMIDs); PROSPERO freeze + deviations (§7b) → WP3.5; knowledge-based critic (not ML) → WP3.4.
- Mid-run cfg-swap (front-end generated cfg feeding the same run's search) is a small follow-on; M3 produces the protocol artifacts + a promotable vertical for a subsequent orchestrated run.

## Execution
Dual-harness WP-M3.1 → … → WP-M3.5, each gated on Codex APPROVE + suite green. Then plan M4.
