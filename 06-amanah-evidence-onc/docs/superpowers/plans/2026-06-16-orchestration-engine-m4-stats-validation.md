# Orchestration Engine — M4 (Stats Registry + Evidence-Strength Gate + Validation) Plan

> **For agentic workers:** Executed via the Claude↔Codex DualReview harness. Each WP gated on Codex `VERDICT: APPROVE` + full suite green. TDD within each WP.

**Goal:** Make the synthesis safe and the validation credible — the do-no-harm + "don't grade your own homework" milestone. Add a coded evidence-strength gate (§7b), a pluggable analysis-method registry with applicability preconditions, and a blinded/pre-registered/sealed-gold-standard benchmark mode for the validation paper.

**Architecture:** Deterministic modules over the existing `stats.json` (PoolResult/HRPoolResult shapes). The evidence-strength gate annotates each pooled outcome with suppression/caveat decisions; the registry validates a chosen method against the actual data; the benchmark mode adds pre-registration + a sealed gold store the orchestrator cannot read mid-run.

**Tech stack:** Python 3.11, existing `core/sr/stats/*`, `core/sr/orchestrator/*`, pytest.

**Spec:** §7b (evidence-strength gate, surrogate/indirect/single-arm), §8 (validation/benchmark mode), §11 (analysis-method registry).

---

## WP-M4.1 — Evidence-strength gate: heterogeneity/sparsity + single-arm firewall
**Files:** Create `core/sr/orchestrator/evidence_strength.py`, `tests/orchestrator/test_evidence_strength.py`.
- `assess(stats: dict, *, i2_max=75.0, k_floor_prop=5, k_floor_hr=3) -> dict`: per pooled outcome/group emit `{outcome, group, k, i2, suppress_headline, require_prediction_interval, non_comparative, reasons[]}`.
- Rules: pooled **proportions are NON-COMPARATIVE** (single-arm) — always flagged, firewalled from comparative claims; any outcome with `I² > i2_max` OR `k < floor` → `suppress_headline=True` + `require_prediction_interval=True` with a reason.
- `evidence_strength_adapter(run_dir, ctx)` reads `stats.json` → writes `evidence_strength.json` (+ records stats.json input hash). Feeds the manuscript export honesty ledger.
- Acceptance: a high-I² (90) proportion → suppress + PI + non_comparative; a clean HR (I² 20, k 4) → not suppressed; k<floor HR → suppressed; proportions always non_comparative.

## WP-M4.2 — Surrogate taxonomy + mandatory NMA indirectness + conclusion linter
**Files:** add to `evidence_strength.py`, `tests/orchestrator/test_evidence_conclusions.py`.
- `classify_endpoint(name) -> "definitive"|"surrogate"` (reuse the protocol_critic token/phrase logic; surrogate = ORR/PFS/MRD/*-free survival; definitive = OS/QoL/mortality).
- Mark indirect/NMA outcomes with a mandatory `grade_indirectness_downgrade=True` + an "indirect evidence" label.
- `lint_conclusion(text, flags) -> list[violation]`: block comparative/superiority language unsupported by a direct adequate-certainty estimate; block surrogate-as-clinical-benefit phrasing.
- Acceptance: ORR flagged surrogate w/ caveat; NMA outcome carries indirectness downgrade; a conclusion claiming "X improves survival" backed only by a surrogate/indirect estimate is flagged.

## WP-M4.3 — Analysis-method registry with applicability preconditions
**Files:** Create `core/sr/orchestrator/method_registry.py`, `tests/orchestrator/test_method_registry.py`.
- Registry of statistical methods; each entry declares applicability preconditions (effect measure, min studies, network-connectedness for NMA, transitivity-required) + a validator.
- `select_method(name, data_summary) -> (ok, warnings)`: fail closed / warn when a chosen method's preconditions aren't met by the data (e.g., fixed-effect with high heterogeneity; NMA on a disconnected network).
- Acceptance: NMA on disconnected network rejected; random-effects on k=2 warns; valid selection passes; unknown method fails closed.

## WP-M4.4 — Benchmark / validation mode
**Files:** Create `core/sr/orchestrator/benchmark.py`, `tests/orchestrator/test_benchmark.py`.
- `start_benchmark(run_dir, *, protocol_hash, gold_path)`: refuses to start without a **pre-registered protocol hash** committed before search; stores a **sealed** reference to the gold standard the orchestrator cannot read during the run; `--blind` flag recorded.
- `benchmark_compare(run_dir, gold)` (no LLM): pre-specified metrics — included-study precision/recall/F1 vs the published set, pooled-estimate ratio + CI overlap, was the published point inside our CI — runnable only AFTER gates clear.
- Acceptance: benchmark refuses to start without a protocol hash; the gold store is not readable via the run API mid-run; compare computes the declared metrics on a fixture.

---

## Self-review
- §7b evidence-strength → WP4.1 + WP4.2; §11 method registry → WP4.3; §8 validation mode → WP4.4.
- Gate wiring into the manuscript export honesty ledger is the small M-final follow-on (manuscript/export adapter), noted separately.

## Execution
Dual-harness WP-M4.1 → … → WP-M4.4, each gated on Codex APPROVE + suite green. M4 completes the engine (M1–M4).
