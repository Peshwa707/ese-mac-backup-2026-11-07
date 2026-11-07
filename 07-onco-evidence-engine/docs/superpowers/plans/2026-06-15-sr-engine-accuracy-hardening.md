# SR-Engine Accuracy & Provenance Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: implement task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax.
> **Execution harness:** DualReview (Claude ↔ Codex). Each Work Package (WP) is one implement/review cycle — one engine implements, the *other* independently reviews and must return `VERDICT: APPROVE` before merge. Roles alternate per WP.

**Goal:** Institutionalize, as first-class engine features, the manual safeguards that produced this project's accuracy — positive-control trial checks, curation-before-pooling, dual-reviewer reconciliation, and verified-reference integrity — plus two search-completeness fixes.

**Architecture:** Additive changes to `core/sr/` and `core/connectors/`; new modules `core/sr/reconcile.py` and `core/sr/references.py`; no breaking changes to existing run outputs (every new gate is fail-safe / opt-in via config until validated).

**Tech Stack:** Python 3.11, pytest, existing engine connectors/agents, NCBI E-utilities + Crossref (already used).

**Provenance:** every gap below was observed in the mm-bcma-early-relapse run (2026-06-15); see `audit` findings inline.

---

## WP1 — Harden the RIS import parser (lock the N2/C5 fix) + tests
**Why:** Embase/Ovid RIS use `N2` for abstract and `C5` for PMID; the original parser read only `AB`/`AN`, silently dropping 87% of abstracts and under-counting overlap. Fixed inline this run — now lock it with tests so it can't regress.

**Files:**
- Modify: `core/connectors/imported.py` (`parse_ris`)
- Create: `tests/connectors/test_imported_ris.py`

- [ ] **Step 1 — Failing test** with real-shape fixtures (Embase + PubMed RIS):
```python
from core.connectors.imported import parse_ris
EMBASE = "TY  - JOUR\nT1  - Teclistamab in MM\nN2  - Teclistamab reduced progression.\nC5  - 42212933\nDO  - 10.1056/x\nER  -\n"
def test_ris_embase_n2_abstract_and_c5_pmid():
    r = parse_ris(EMBASE)[0]
    assert r["abstract"].startswith("Teclistamab reduced")
    assert r["pmid"] == "42212933"
    assert r["doi"] == "10.1056/x"
def test_ris_classic_ab_an_still_work():
    r = parse_ris("TY  - JOUR\nT1  - X\nAB  - abstract here\nAN  - 12345678\nER  -\n")[0]
    assert r["abstract"] == "abstract here" and r["pmid"] == "12345678"
```
- [ ] **Step 2 — Run, expect FAIL** (if reverted): `pytest tests/connectors/test_imported_ris.py -v`
- [ ] **Step 3 — Confirm `parse_ris` maps `AB|N2`→abstract and `AN|ID|C5`→pmid** (already patched; verify present).
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "test: lock RIS N2/C5 parsing for Embase exports"`

**Acceptance:** both tests pass; coverage includes Embase-style and classic RIS.

---

## WP2 — Source pagination (Europe PMC, ClinicalTrials.gov)
**Why:** Europe PMC returned only 200 and ClinicalTrials.gov only 20 (single page) — silently undersold both sources.

**Files:**
- Modify: `core/connectors/europepmc.py` (`search`), `core/connectors/clinicaltrials.py` (`search`)
- Create: `tests/connectors/test_pagination.py`

- [ ] **Step 1 — Failing test** (mock the HTTP client to return 2 pages with a cursor/`nextPageToken`, assert all records returned):
```python
async def test_europepmc_paginates(monkeypatch):
    # mock _request to return page1 (nextCursorMark) then page2 (end); expect len == sum of both pages
    ...
    assert len(result.citations) == 250   # 200 + 50, not capped at 200
```
- [ ] **Step 2 — Run, expect FAIL** (currently single page).
- [ ] **Step 3 — Implement pagination:** Europe PMC via `cursorMark`/`nextCursorMark` loop until `nextCursorMark` repeats or `hitCount` reached, capped by a `max_records` arg (default 5000). ClinicalTrials.gov v2 API via `nextPageToken` loop. Respect a hard `max_records` to avoid runaway.
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(connectors): paginate Europe PMC + ClinicalTrials.gov retrievals"`

**Acceptance:** both connectors return all results up to `max_records`; default cap documented; no infinite loop on repeated cursor.

---

## WP3 — Publication-type capture + deterministic pre-screen filter
**Why:** We manually efetched `PublicationType` to exclude editorials/letters and to route Embase conference abstracts. Pub-type is captured for PubMed (`study_type`) but not used as a deterministic filter, and Embase `M3` is not captured.

**Files:**
- Modify: `core/connectors/imported.py` (capture `M3`→`study_type`), `core/connectors/pubmed.py` (ensure pub-types list retained)
- Create: `core/sr/prescreen.py` (deterministic type filter), `tests/sr/test_prescreen.py`

- [ ] **Step 1 — Failing test:**
```python
from core.sr.prescreen import classify_by_type
def test_editorial_excluded_and_conf_flagged():
    recs = [{"pmid":"1","study_type":"Editorial"}, {"pmid":"2","study_type":"Conference Abstract"},
            {"pmid":"3","study_type":"Randomized Controlled Trial"}]
    out = classify_by_type(recs)
    assert out["1"]["bucket"] == "exclude_by_type"
    assert out["2"]["bucket"] == "conference_abstract"   # -> sensitivity, not primary
    assert out["3"]["bucket"] == "screen"                 # passes to normal screening
```
- [ ] **Step 2 — Run, expect FAIL.**
- [ ] **Step 3 — Implement `classify_by_type`:** map types to buckets {`exclude_by_type` (Editorial/Letter/Comment/News/Erratum/Note/Review — unless also a trial type), `conference_abstract`, `screen`}; never auto-exclude `Clinical Trial`/`RCT`/`Article`. Capture `M3` in `imported.py to_citation` into `study_type`.
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(sr): deterministic publication-type pre-screen filter + Embase M3 capture"`

**Acceptance:** editorials/letters excluded deterministically (logged, not screened); conference abstracts routed to a sensitivity bucket; trials/articles always reach screening; counts surfaced for PRISMA.

---

## WP4 — Named-trial sentinel / positive-control gate ⭐ (highest accuracy payoff)
**Why:** Criteria silently excluded a pivotal NEJM trial (MajesTEC-9); `_named_trial_capture()` only *logs*. Make named trials a hard canary.

**Files:**
- Modify: `core/sr/pipeline.py` (post-screening assertion), `core/sr/config.py` (read `named_trials` + new `sentinel_min_stage`)
- Create: `core/sr/sentinel.py` (the check), `tests/sr/test_sentinel.py`

- [ ] **Step 1 — Failing test:**
```python
from core.sr.sentinel import check_named_trials
def test_sentinel_fails_when_named_trial_excluded():
    named = ["MajesTEC-9"]
    screened = [{"pmid":"42212933","title":"Teclistamab in MM with One to Three Previous Lines","decision":"exclude"}]
    report = check_named_trials(named, screened, min_stage="advance")
    assert report["passed"] is False
    assert "MajesTEC-9" in report["violations"]
def test_sentinel_passes_when_advanced():
    screened = [{"pmid":"42212933","title":"Teclistamab in MM with One to Three Previous Lines","decision":"include"}]
    assert check_named_trials(["MajesTEC-9"], screened, min_stage="advance")["passed"] is True
```
- [ ] **Step 2 — Run, expect FAIL.**
- [ ] **Step 3 — Implement `check_named_trials`:** match each `named_trials` entry to screened records by title/programme token (case-insensitive, hyphen-insensitive); a trial *passes* if ≥1 matching record reached `min_stage` (include/uncertain = "advance"); return `{passed, violations[], matched{}}`. Wire into `pipeline.py` after screening: on failure, raise/flag loudly (config `sentinel_strict: true` → raise; else emit a prominent warning into the run report).
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(sr): named-trial sentinel gate (positive control for screening)"`

**Acceptance:** a run that excludes a configured named trial fails loudly (strict) or emits a top-level warning; passing runs record the matched stage per named trial in the audit.

---

## WP5 — Curate effect estimates before pooling (trial dedup + comparator validation) ⭐
**Why:** Naive HR pooling double-counted CARTITUDE-4 (two data cuts), included a wrong-comparator (gamma-secretase) study, and a line-mismatch trial → meaningless pooled estimate.

**Files:**
- Modify: `core/sr/stats/prepare.py` (`hr_rows`), `core/sr/stats/run.py`
- Create: `tests/sr/stats/test_curation.py`

- [ ] **Step 1 — Failing test:**
```python
from core.sr.stats.prepare import curate_for_pool
def test_dedup_datacuts_and_comparator_filter():
    rows = [
      {"trial":"CARTITUDE-4","pmid":"37272512","hr":"0.26","data_cut":"2023","comparator":"PVd/DPd"},
      {"trial":"CARTITUDE-4","pmid":"41519141","hr":"0.29","data_cut":"2026","comparator":"PVd/DPd"},  # newer cut
      {"trial":"GSI-study","pmid":"40930223","hr":"0.47","comparator":"GSI (crenigacestat)"},          # wrong anchor
    ]
    kept = curate_for_pool(rows, anchor_terms=["dpd","pvd","dvd","kd","daratumumab","pomalidomide","standard"])
    pmids = {r["pmid"] for r in kept}
    assert pmids == {"41519141"}          # one estimate/trial (newest cut), GSI dropped
```
- [ ] **Step 2 — Run, expect FAIL.**
- [ ] **Step 3 — Implement `curate_for_pool`:** (a) group by `trial` (or normalized study_name), keep the most-recent `data_cut`/follow-up; (b) drop rows whose `comparator` doesn't match any `anchor_terms`; (c) return kept rows + a dropped-with-reason log. Call it inside `hr_rows`/`run.py` before pooling; surface the drop log in stats output.
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(stats): curate effect estimates (trial dedup + comparator gate) before pooling"`

**Acceptance:** pooling uses exactly one estimate per trial against the SOC anchor; dropped estimates are logged with reasons; existing single-cut runs unchanged.

---

## WP6 — Reconciliation module: Cohen's κ (nominal + weighted) + consensus rule
**Why:** Dual-reviewer reconciliation + κ were built as one-off scripts this run; κ was nominal only (dragged down by harmless exclude↔uncertain adjacency).

**Files:**
- Create: `core/sr/reconcile.py`, `tests/sr/test_reconcile.py`

- [ ] **Step 1 — Failing test:**
```python
from core.sr.reconcile import cohen_kappa, reconcile
def test_weighted_kappa_higher_than_nominal_for_adjacent_disagreement():
    a = ["exclude"]*90 + ["exclude"]*10
    b = ["exclude"]*90 + ["uncertain"]*10   # adjacent disagreements only
    nom = cohen_kappa(a,b, weights=None)
    wt  = cohen_kappa(a,b, weights="linear", order=["include","uncertain","exclude"])
    assert wt > nom
def test_union_consensus_protects_include():
    r1={"x":"exclude"}; r2={"x":"include"}
    out = reconcile(r1,r2)
    assert "x" in out["advance"]          # any-include/uncertain advances
```
- [ ] **Step 2 — Run, expect FAIL.**
- [ ] **Step 3 — Implement:** `cohen_kappa(a,b,weights=None|'linear'|'quadratic',order=...)`; `reconcile(r1,r2)` → `{kappa_nominal, kappa_weighted, advance[], exclude_both[], disagreements[]}` using the conservative union rule (advance if either include/uncertain). Pure-python, no deps.
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(sr): reconciliation module (nominal+weighted kappa, union consensus)"`

**Acceptance:** weighted κ implemented and ≥ nominal for adjacent-only disagreement; union rule never drops an include; outputs feed the PRISMA/audit.

---

## WP7 — Reference-integrity module + cite-or-omit gate in manuscript export
**Why:** Verified-reference library + integrity checker were external (`references.json`, `manuscript_refs.py`). Promote to a first-class module so the bibliography structurally cannot ship a fake/broken reference.

**Files:**
- Create: `core/sr/references.py` (library load/verify, integrity check, Vancouver render, key→number), `tests/sr/test_references.py`
- Modify: `core/sr/manuscript/assemble.py` (fail export on broken/orphan citations)

- [ ] **Step 1 — Failing test:**
```python
from core.sr.references import check_integrity, render_vancouver
LIB={"A2023":{"key":"A2023","pmid":"1","verified":True,"authors":"A et al.","title":"T","journal":"J","year":"2023"}}
def test_broken_citation_detected():
    rep = check_integrity("text cites [A2023] and [GHOST]", LIB)
    assert rep["broken"] == ["GHOST"] and rep["orphan"] == []
def test_vancouver_render_numbers_by_appearance():
    out = render_vancouver("see [A2023]", LIB)
    assert out[0].startswith("1. A et al.")
```
- [ ] **Step 2 — Run, expect FAIL.**
- [ ] **Step 3 — Implement:** port `manuscript_refs.py` logic (integrity check ignoring non-citation tokens, Vancouver render by first appearance, key→number map); add `verify_library()` that live-checks PMIDs/DOIs (PubMed/Crossref) and marks `verified`. In `assemble.py`, call `check_integrity` and **raise/refuse export if any broken citation or any unverified entry**.
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(sr): verified reference library + cite-or-omit manuscript gate"`

**Acceptance:** manuscript export fails on broken/orphan/unverified citations; Vancouver list auto-generated; integrity report in the honesty audit.

---

## Out of scope (follow-up plans)
- Dual-engine "Reviewer 2 = different model" wiring (larger; reconcile.py here is the prerequisite).
- MAIC + Guyot KM-reconstruction module.
- RoB2/ROBINS-I pipeline wiring; full-text HTML-as-PDF guard + section-targeted extraction; stable non-PMID record IDs; R/NMA dependency installer + star-network reporting.

## Self-review
- **Coverage:** WP1–7 map to audit gaps #1,#2,#3,#6,#7,#9,#13 (the Tier-1 accuracy set + two completeness wins). Remaining gaps explicitly deferred above.
- **Fail-safe:** every new gate defaults to non-breaking (sentinel warn-not-raise unless `sentinel_strict`; curation only drops true duplicates/wrong-comparators; reference gate only active in export).
- **Types:** `check_named_trials(named, screened, min_stage)`, `curate_for_pool(rows, anchor_terms)`, `cohen_kappa(a,b,weights,order)`, `reconcile(r1,r2)`, `check_integrity(text,lib)`, `render_vancouver(text,lib)` — names consistent across tasks.
