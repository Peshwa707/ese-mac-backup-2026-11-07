# Codex Task — Fresh-eyes review-and-fix pass over `main`

## Mission
Do a systematic debug-and-fix review of the engine as it stands on `main` (HEAD = the `#16` live-robustness merge). This is a **quality/correctness hardening pass**, NOT new features. Find real defects, dead code, honesty-gate gaps, and cross-vertical bleed; fix them surgically; leave every honesty invariant intact. Propose your PLAN first for review before implementing.

Branch from updated `origin/main`. One branch → tests green → PR; Claude/PAI then verifies (full suite + that no honesty gate weakened + spot live behavior).

## Context — what was just merged (so you don't re-litigate it)
`#16` made the live pipeline robust: chunked screening with per-record fallback (`core/sr/screen.py`), narrative graceful degradation around a still-strict claim-check (`core/tools/builtin.py` `_run_narrative` / `_filter_narrative_claims`), and tolerant proportion parsing (`core/sr/stats/prepare.py` `parse_proportion`). These were live-validated end-to-end (retmax=50 cloud run). Do NOT redo them; review them as part of the whole.

## Confirmed leads (verified by a live retmax=50 cloud run — start here)
1. **Narrative transparency metric is misleading.** `_run_narrative` writes `claim_check: {checked: N, dropped: M}` where `dropped` counts BOTH no-citation drops AND claim-check rejections, while `checked` counts ONLY sentences that reached `_claim_check_text`. Observed live: `checked: 10, dropped: 16` — i.e. "16 dropped of 10 checked," which is nonsensical to a reader. Fix: report a coherent breakdown (e.g. `sentences_considered`, `dropped_no_citation`, `claim_checked`, `dropped_unsupported`, `kept`) so the numbers reconcile. Update the test(s) accordingly. Pure transparency/reporting fix — does NOT change what gets dropped.
2. **Drafter still over-claims.** Same run dropped 16 sentences from a single draft (the honest fallback then carried it). The tightened drafting system prompt in `_run_narrative` only partly achieved "conservative drafting → fewer drops." Tighten the drafter further so it asserts ONLY brief-supported, citation-bearing statements and omits GRADE/certainty/review-level synthesis at draft time (those are added downstream, under the lock). Goal: materially fewer claim-check rejections on the same inputs, WITHOUT weakening claim-check. Add/adjust a test asserting the drafter prompt forbids ungrounded review-level claims.

## Systematic review dimensions (sweep all; fix what's real)
- **Cross-vertical / project bleed (priority — recurring concern):** any oncology-specific (myeloma/CAR-T/BsAb) field name, default, criterion, template, or constant hard-coded into GENERIC core paths (`core/sr/**`, `core/tools/**`, `core/llm/**`, `core/render/**`) instead of living under `verticals/<name>/`. The generic core must be domain-neutral; oncology lives only in its vertical. Report each bleed with file:line and move it to the vertical (preserve back-compat).
- **Honesty-gate integrity:** verify — and add a test wherever one is missing — that each gate actually fails closed: cite-or-omit, exclude-and-count (no fabricated denominators), claim-check cloud-only (`_CLOUD_ONLY_STAGES`), NR-not-fabricated, NNT/NNH direction config-driven, and the conclusion hard-lock (`core/sr/signoff.py`) gating GRADE certainty + conclusions + recommendations in every renderer (manuscript, narrative, decision-support).
- **Error-path robustness:** broad `except` that swallows real failures; `RuntimeError`/parse fallbacks that could silently drop data without the missing→uncertain / exclude-and-count guardrail; missing-file paths that crash instead of failing closed with a clear message.
- **Dead / unreachable / duplicated code:** unused functions, duplicate logic that drifted between the native pipeline and the tool layer (e.g. screening/extraction implemented twice), leftover `TODO`/`FIXME`, stale prompts.
- **Config-vs-hardcode:** magic numbers/thresholds that should be config (chunk sizes, retmax defaults, downgrade thresholds, k-minimums).
- **Test gaps:** any fix above ships with a focused offline test; note (don't necessarily fill) larger coverage holes you find.

## Honesty / safety invariants (NON-NEGOTIABLE — do not weaken to "fix" anything)
- claim-check stays strict and cloud-only; narrative/manuscript degrade AROUND it, never relax it.
- Screening keeps missing→uncertain + hallucinated-PMID→dropped on every path (batched, fallback, harness).
- Parser keeps exclude-and-count for ambiguous values; never fabricate events/denominators.
- Conclusion lock + scorecards unchanged; nothing flips to `externally_validated=True`.

## Process
1. **Propose a PLAN first** (categorized findings with file:line + proposed fix + risk) and STOP for review before editing.
2. After approval: implement surgically, smallest diff per fix; group related fixes.
3. `python -m pytest -q -m "not live"` fully green (current baseline = 365 passed, 2 deselected); R-gated tests still pass where CRAN R present.
4. PR with a findings/fix table; note anything deferred to backlog.

## Out of scope (separate follow-ups — note as backlog, do NOT do here)
Search-strategy standardization (the corpus-divergence work); dual independent review (Claude R1 + GPT R2 + human adjudicator + Cohen's κ); any new statistic, output profile, web/MCP/agent surface, or ML. No live API calls in this PR — offline tests mock the LLM; live acceptance is operator-run post-merge.
