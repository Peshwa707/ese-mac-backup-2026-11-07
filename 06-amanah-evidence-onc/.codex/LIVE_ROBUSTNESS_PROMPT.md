# Codex Task — Live-robustness fixes (3 bugs the protocol live run exposed)

## Mission
A real cloud run of `full-review` on a 100-record protocol corpus exposed three robustness bugs that block a clean end-to-end run. Fix all three, in priority order, WITHOUT weakening any honesty gate (claim-check stays strict; exclude-and-count stays; cite-or-omit stays). These are surgical fixes to existing code, not new features. Branch from updated `origin/main`.

## Context — observed failures (from the live run)
1. **Screening crashes on large corpora.** `full-review` at retmax=100 failed at `screen` with `RuntimeError: LLM returned no parseable structured output` — even on the cloud API. The in-process screening sends too many records in one batched `ScreeningBatch` structured call; one malformed item fails the whole parse, and large batches make that likely.
2. **Narrative is blocked entirely when claim-check rejects claims.** The narrative drafted review-level / GRADE / RoB claims unsupported by the abstract-level sources; `claim-check` CORRECTLY rejected them — but the result was NO `narrative.json` at all (all-or-nothing), blocking `manuscript`.
3. **Pooling produced no outcomes** because extracted values (free-text from the live LLM, e.g. "ORR 84.6% (95% CI ...)", "84 of 110", "0.85") are not normalized enough for the proportion-row parser, so every study was excluded.

## Build — fix 1 (PRIORITY): robust chunked screening with per-record fallback
- `core/sr/screen.py` `screen_batch(records, cfg)`: make it robust at ANY corpus size.
  - CHUNK `records` into small batches (default size ~12; configurable) and screen chunk-by-chunk, so a single bad item can never lose the whole corpus.
  - For each chunk: call the batched `ScreeningBatch` parse; on `RuntimeError`/parse failure, RETRY once; if it still fails, FALL BACK to PER-RECORD screening for that chunk (one `ScreeningResult` per record via `llm.parse`, which is far more reliable). Assemble the chunk's results either way.
  - Preserve the EXISTING guardrails unchanged: every input PMID gets a decision (missing/omitted → `uncertain` via `_missing_as_uncertain`); hallucinated PMIDs (not in the chunk's `valid_ids`) are dropped. The fallback path must apply the same guardrails.
  - This benefits both the `screen` tool and the native pipeline. (The harness/local paths already screen per-record; this fixes the in-process cloud/local-via-client path.)
- Make the chunk size a constant/param so it can be tuned; keep batched-first (efficient) with per-record only as the fallback.

## Build — fix 2: narrative degrades gracefully (drop unsupported claims, don't fail)
- Do NOT weaken `claim-check` — it is the safety net and must stay strict. Instead make the `narrative` path DEGRADE gracefully, mirroring the manuscript's existing sentence-level cite-or-omit (`core/synthesis/synthesize.py` `_audit_manuscript`):
  - After drafting, run the claim/sentence audit; DROP the sentences/claims claim-check rejects as unsupported; KEEP the supported ones; still produce `narrative.json` / `narrative_final.json` with only grounded content, plus a transparency field recording how many claims were dropped and why.
  - If ALL claims are dropped, emit a narrative that honestly says the abstract-level evidence did not support drafted prose (not a crash, not empty-file failure) so `manuscript` can still assemble.
- ALSO tighten the narrative drafting instruction (`core/sr/narrative.py` brief/draft path) to reduce rejections at the source: instruct the drafter to assert ONLY what the grounded brief/extractions support — no review-level synthesis, no GRADE/RoB conclusions unless those artifacts exist and are signed off. Conservative drafting → fewer drops.
- Honesty invariant: dropped claims are DROPPED (never kept); the conclusion lock is unchanged.

## Build — fix 3: tolerant proportion-row parsing (recover poolable studies)
- `core/sr/stats/prepare.py` `parse_pct` (and the `outcome_rows` path): accept the common real-world value formats the live extraction produces, while keeping exclude-and-count for genuinely ambiguous values:
  - "84%", "84.6%", "ORR was 84%", "84.6% (95% CI 78-90)" → 84.6 (percent embedded in text/with CI).
  - "84 of 110" / "84/110" → compute 76.4% AND yield events=84, n=110 directly.
  - a bare decimal in [0,1] (e.g. "0.85") → 85.0%.
  - Keep the 0–100 range guard; values that cannot be resolved to a single percent + denominator remain excluded-and-counted (unchanged honesty).
- Do not become reckless: when a string has multiple numbers with no clear single proportion, EXCLUDE-and-count rather than guess.

## Honesty / safety gates (non-negotiable)
- `claim-check` is NOT relaxed — fix 2 makes the narrative degrade around it, not weaken it.
- Screening fallback keeps the missing→uncertain + hallucinated→dropped guardrails.
- Parser keeps exclude-and-count for genuinely ambiguous values; no fabricated denominators.
- Conclusion lock + scorecards unchanged; nothing flips to `externally_validated=True`.

## Tests (offline-first)
- Fix 1: monkeypatch `llm.parse` so the batched `ScreeningBatch` call raises but per-record `ScreeningResult` succeeds → `screen_batch` still returns a full decision set via fallback (one per input PMID), with missing→uncertain and hallucinated dropped; a 40-record input is chunked (assert >1 chunk / >1 parse attempt).
- Fix 2: a draft with some unsupported claims + a `claim-check` mock that rejects them → `narrative.json` is produced with ONLY the supported claims and a dropped-count field (not an empty/failed step); all-rejected → an honest non-empty "no abstract-supported prose" narrative; `manuscript` still assembles.
- Fix 3: parametrized `parse_pct`/`outcome_rows` tests for "84%", "ORR 84.6% (95% CI ...)", "84 of 110", "0.85" → correct percent (+ events/n where given); an ambiguous multi-number string → excluded-and-counted.
- Full offline suite green (no regression to the existing 358); `claim-check` cloud-only + lock tests still pass.

## Verification before done
- `python -m pytest -q -m "not live"` fully green.
- With CRAN R, the R-gated stats tests still pass.
- (Post-merge live acceptance, operator-run, key from keychain — NOT in this PR) re-run `full-review` on the protocol corpus at retmax=100 and confirm: screening completes (no parse crash), a narrative is produced (with any unsupported claims dropped + counted), and pooling yields ≥1 outcome where the extraction gave parseable proportions.

## Out of scope — do NOT do here
Dual-review orchestration; changing claim-check's strictness; new statistics; web/MCP/agent; the search-strategy standardization (separate follow-up). Note these as backlog in the PR description.

## Implementation order
1. Fix 1 (chunked screening + per-record fallback) + tests — unblocks everything.
2. Fix 3 (tolerant proportion parser) + tests.
3. Fix 2 (narrative graceful degradation + tighter drafting) + tests.
4. Full offline suite green; PR description noting the post-merge live acceptance + backlog.

## Cross-cutting (validated workflow)
Reuse existing patterns (`_missing_as_uncertain`, `_audit_manuscript` cite-or-omit, `parse_pct`); match style; conservative scorecards; offline tests mock the LLM (no live). Propose your PLAN first for review before implementing. After you push, Claude/PAI verifies — full suite + that claim-check stayed strict (narrative drops, not keeps) + screening fallback guardrails + parser exclude-and-count.
