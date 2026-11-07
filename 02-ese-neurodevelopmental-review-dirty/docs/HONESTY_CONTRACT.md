# Honesty Contract

Non-negotiable invariants carried from onco-evidence-engine.

1. **Cite-or-omit** — every substantive claim has a verifiable PMID/DOI/NCT or is dropped.
2. **NR/ESTIMATED** — not reported stays NR; estimates are flagged, never silent.
3. **Reviewer 1 only** — AI screens, extracts, drafts; never the final clinical word.
4. **Signed gates** — screen and extract require human `ese approve` with a real actor name.
5. **Export fail-closed** — `ese export` refuses when `export_gate()` is not clear.
6. **Reviewer 2 sign-off** — `--approve-conclusions` unlocks conclusion-bearing rendering.

This tool is for evidence-synthesis professionals, not direct patient care.
