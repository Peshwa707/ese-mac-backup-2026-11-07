# Codex Task — Product-Vision Master Brief + Alignment Review (READ-ONLY)

You are reviewing this codebase against its product vision to tell us whether our development is **on track**. This is an **audit that produces a written assessment** — do NOT edit code, do NOT open a fix PR. Read the code, compare it to the vision below, and report. Propose your reading PLAN first if helpful, then deliver the assessment.

---

# PART 1 — THE PRODUCT VISION (the north star to measure against)

## 1. What we are building (one sentence)
An **OpenEvidence-style evidence engine**: for *every* clinical question (a PICO), it runs a **fresh, full literature search + analysis from scratch**, pools and summarizes the evidence with real statistics, and presents a grounded synthesis where **the human draws the final conclusion** — never the machine.

## 2. The soul of the product — non-negotiable honesty invariants
These are not features; they are the reason the tool can be trusted. Every part of the system must uphold them, and nothing may be weakened to make something else work:
- **Cite-or-omit** — every substantive claim carries a verifiable source (PMID); unsupported sentences are dropped, not kept.
- **Exclude-and-count** — ambiguous/unparseable data is excluded and *honestly counted*, never coerced or fabricated. No invented denominators, events, or numbers.
- **NR-is-not-zero** — "not reported" is preserved as NR, never silently turned into a value.
- **Claim-check** — an independent verification stage is the safety net; it is **cloud-only forever** (`_CLOUD_ONLY_STAGES`), and never relaxed. Other stages degrade *around* it, never through it.
- **Fail-closed gates** — when a precondition isn't met (no data, no R packages, no comparator), the system reports "not performed / infeasible" with a reason; it never fakes a result.
- **Human-in-the-loop conclusion hard-lock** — final conclusions, GRADE certainty, and clinical recommendations are **withheld behind a human sign-off** (`core/sr/signoff.py`). Drafts render a DRAFT/LOCKED banner until a reviewer approves. The machine is **Reviewer 1**, never the final word.

## 3. Capability vision (the methods)
A fresh search → screen → extract → full-text → risk-of-bias → GRADE → **meta-analysis** → narrative → manuscript pipeline, where meta-analysis spans **multiple methods**, each honesty-gated:
- Proportion pooling (single-arm event rates).
- Treatment comparison: **RR / OR / risk difference / NNT** (NNT/NNH direction config-driven, "not estimable" when the CI crosses null — never a fabricated NNT).
- Risk-factor association (OR/RR, highest-vs-lowest) for epidemiology.
- Hazard ratio / log-effect inverse-variance pooling.
- Continuous outcomes (mean difference; median/IQR→mean/SD imputation, flagged as imputed).
- Meta-regression; network meta-analysis (feasibility-gated); dose-response.
All statistics run through real R (`meta`/`metafor`) via deterministic scripts — not the LLM.

## 4. Output vision (the deliverables)
Multiple review *types* off the same grounded spine: systematic review, meta-analysis, **narrative review, case-series review, case-report review**, plus scoping / rapid / living variants. Each renders a manuscript that respects the conclusion lock.

## 5. Architecture vision
- **Domain-agnostic generic core + verticals.** `core/**` is neutral machinery; everything disease-specific (field names, criteria, arm labels, templates) lives under `verticals/<name>/`. The extraction schema is the source of truth for what gets pooled. **No vertical may bleed into core**, and no vertical may bleed into another.
- **Agentic tool platform.** Discrete tools over a run-folder (artifacts-as-state); `recipes` compose tools into workflows (`quick-look`, `full-review`). Each tool has a **backend policy** with per-call override.
- **Three LLM backends.** `cloud` (Anthropic API), `local` (Ollama, structured-output-constrained), `harness` (agent-mediated via a Claude Max OR ChatGPT Pro subscription). claim-check is pinned to cloud regardless.
- **Maturity dial — "everything graduates on evidence."** Posture `cloud → prefer-local → local`; each tool carries a **scorecard** (`internally_tested`, `externally_validated`). We run **cloud-default now**, and a capability only moves local/harness once benchmarks earn it. **Cost mandate: minimize online API, maximize local LLM** as maturity allows — without ever compromising Part 2.

## 6. Quality & process vision
- **Dual independent review** (target): the machine as **Reviewer 1**, a second independent agent (Claude R1 + GPT R2) as **Reviewer 2**, and the **human as adjudicator** — mapping to the PRISMA dual-independent-review requirement, with inter-reviewer agreement (Cohen's κ).
- **ML for continuous improvement** (target): active-learning screen prioritization + a feedback store that learns from human corrections across runs; classifier-vs-LLM disagreement routes to human review.
- **Blind benchmark discipline** — every method/output is validated against *published* meta-analyses before it's trusted; results recorded in a running benchmark log (regression net).
- **Build discipline** — phased (Gates → Cost → Capability → ML), branch → PR → tests green → benchmark → merge, one phase at a time; nothing proceeds until the prior step is verified. **Codex implements; Claude/PAI plans + verifies.**
- **Beginner-owned** — the operator is a coding/ML beginner; the system must stay legible (plain-language specs, clear errors, honest provenance).

## 7. Where we believe we are (verify this against reality — do not assume it's accurate)
Believed BUILT: the full search→…→manuscript pipeline; the meta-analysis family (proportion/HR/binary/continuous/meta-reg/NMA/dose-response); the tool platform + registry + recipes; the harness backend (Claude Max + ChatGPT Pro); the HITL conclusion lock; per-stage model provenance; the posture/maturity-dial + scorecards; genericization into generic-core + verticals; recent live-robustness hardening (chunked screening, narrative graceful degradation, tolerant parsing) and a cross-vertical-bleed hardening pass. Benchmarks: at least one published meta-analysis reproduced blind.
Believed PARTIAL / NOT YET: a fully-exercised **local (Ollama) backend** at parity; **dual independent review** orchestration + κ; **ML** active-learning + feedback store; **search-strategy standardization** across reviewers; some **output profiles** (case-series / case-report / narrative as first-class profiles).

---

# PART 2 — THE REVIEW TASK (read-only; deliver a written assessment)

Walk the actual code and judge it against Part 1. Produce:

1. **Alignment scorecard** — for each vision pillar (honesty invariants; HITL lock; each meta-analysis method; each output type; generic-core/vertical separation; tool platform; three backends; maturity dial + scorecards; cost mandate; dual review; ML; benchmark discipline), rate **On-track / Partial / Missing / Drifted** with **file:line evidence** for the rating. Be specific and skeptical — "it exists" is not "it works as envisioned."

2. **Honesty-invariant integrity** — confirm (or refute, with evidence) that each invariant in §2 is actually enforced end-to-end and fails closed. Flag any path where a gate can be bypassed, any place a value could be fabricated/coerced, or any renderer that could leak a locked conclusion. This section is the most important.

3. **Drift & scope-creep** — anything in the code that is NOT in the vision (half-built features, dead experiments, a vertical's logic leaking into core, abstractions that outran their need), and anything in the vision that the code has quietly drifted away from.

4. **Foundation readiness** — for the not-yet-built pillars (local backend at parity, dual review, ML, remaining output profiles), judge whether the *current* seams/abstractions can carry them, or whether we'll have to refactor to get there. Name the specific seam (file/interface) for each.

5. **Gap list → recommended next moves** — an ordered, plain-language list of what to close next to stay on track, each tagged effort (S/M/L) and which vision pillar it serves. Respect our phase order (Gates → Cost → Capability → ML) and our "verify before proceed" rule. Plain language — the operator is a beginner.

6. **Top risks** — the 3–5 things most likely to undermine the vision if left unaddressed, with why.

## Constraints
- **Read-only.** No edits, no PR, no new files except your written report. If you find a bug too tempting to leave, list it in the gap section — do not fix it here.
- **Evidence-based.** Every claim cites file:line. Distinguish "verified by reading the code" from "inferred."
- **Honest about uncertainty.** If you can't confirm something works without running it live, say so and mark it unverified rather than assuming.
- Baseline state: `main` at the latest hardening merge; full offline suite green (~368 tests). Note the commit you reviewed.
