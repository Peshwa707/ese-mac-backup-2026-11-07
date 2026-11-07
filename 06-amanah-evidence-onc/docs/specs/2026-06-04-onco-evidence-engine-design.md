# Onco-Evidence Engine — Design Spec

**Date:** 2026-06-04
**Status:** Approved for planning (input to `/ultraplan`)
**Author:** NeuroLogic + PAI (Claude Opus)

---

## 1. Purpose & Scope

A grounded clinical-evidence engine that takes a **de-identified oncology patient case** and produces, from the *same* literature-grounded analysis:

1. **Clinical decision support** — evidence-based management options, PMID/NCT-cited, OpenEvidence-style point-of-care reasoning.
2. **Publication-grade case report** — a CARE-guideline manuscript draft (intro, presentation, discussion, lit review) exportable for journal submission.

**Design stance:** *Oncology-first in behavior, generic-by-construction in architecture.* The reusable research engine (`core/`) is domain-agnostic; everything cancer-specific lives in `verticals/oncology/` as data, config, and templates. Adding a future research domain = a new `verticals/<x>/` folder, zero engine changes.

**Out of scope (v1):** autonomous/patient-facing advice (this is clinician decision *support*); licensed NCCN/ASCO/ESMO content reuse (deferred to a gated tier — see §7); the consumer app UI (API-first now, app later).

---

## 2. Architecture

```
onco-evidence-engine/
├── core/                          # domain-agnostic — the reusable research engine
│   ├── connectors/                # pubmed, clinicaltrials, openfda, guidelines (interface + PDQ impl)
│   ├── grounding/                 # de-id scrub, query expansion, retrieval, ID-verifier, cite-or-omit
│   ├── synthesis/                 # parallel orchestrator, evidence grading, conflict detection, claim-checker
│   ├── render/                    # template-driven → PDF / HTML / JSON
│   └── api/                       # FastAPI boundary (stable contract for any future app)
├── verticals/oncology/            # cancer-specific = data, config, templates ONLY
│   ├── search_strategies.yaml
│   ├── guideline_refs.yaml
│   ├── schema.yaml                # staging, biomarkers, lines of therapy, ECOG
│   └── templates/                 # care_report.html, decision_support.html
├── tests/
└── (future) app/                  # consumes core/api — built later
```

**Inviolable boundary rule:** No oncology-specific string is ever hardcoded in `core/`. If the engine needs domain knowledge, it reads it from the active vertical config. This single rule is what makes reuse nearly free.

**PAI skill entry point** lives separately at `~/.claude/skills/oncology-case-report/SKILL.md` — a thin wrapper that shells into this engine (same pattern as the existing `oncology-weekly-digest` skill calling `generate_digest_pdf.py`). This keeps the skill discoverable by PAI while real code lives in a proper, reviewable git repo.

**Tech stack:** Python + FastAPI + Pydantic (matches the existing digest skill and Quran app patterns). PDF rendering via the Chrome headless-print approach already used in `generate_digest_pdf.py`.

---

## 3. Data Flow

```
case text
  → ① DE-ID SCRUB        PHI removed; HARD STOP if PHI survives before any external call
  → ② STRUCTURE          parse into oncology schema: dx, stage, biomarkers, prior lines, ECOG
  → ③ QUERY EXPANSION    MeSH terms, synonyms, drug brand↔generic, per source
  → ④ PARALLEL RETRIEVAL PubMed ‖ ClinicalTrials.gov ‖ openFDA ‖ guidelines(PDQ)
  → ⑤ ID VERIFICATION    every PMID/NCT/DOI must resolve via a real API round-trip
  → ⑥ EVIDENCE GRADING   study-type hierarchy + recency; conflicts flagged, not averaged
  → ⑦ SYNTHESIS          cite-or-omit enforced
  → ⑧ ADVERSARIAL CHECK  independent agent verifies each claim is supported by its cited source
  → ⑨ OUTPUT             decision-support view  +  optional CARE manuscript
  → render PDF / HTML / JSON  + provenance audit log
```

---

## 4. Anti-Hallucination Safeguards (defense-in-depth)

Each layer is independent; a failure in one is caught by others.

1. **Retrieval-only grounding** — the model may cite *only* from retrieved results. Closed-book recall is forbidden.
2. **ID verification round-trip** — every PMID/NCT/DOI must resolve against the live API or it is rejected. Eliminates invented citations.
3. **Cite-or-omit** — every clinical claim carries ≥1 verified citation; uncited sentences are stripped or flagged.
4. **Quote-anchoring (span grounding)** — each finding must be backed by an extracted snippet from the real abstract/source text, not a free paraphrase.
5. **Adversarial claim-checker** — an independent agent verifies the cited source *actually supports* the claim. Catches "real citation, wrong claim."
6. **Empty = empty** — zero results returns "no evidence found," never backfilled from memory.
7. **Conflict surfacing** — contradicting studies are shown side-by-side, never silently averaged.
8. **Evidence grade + confidence** surfaced on every claim; low-evidence claims labeled.
9. **Provenance audit log** — full query → results → citation trail saved per report (reproducible, defensible).
10. **Human-in-the-loop framing** — clinician decision support, not autonomous advice (safety + liability).

> This stack matches OpenEvidence's retrieval-grounded, citation-first posture and exceeds typical RAG via layers 2, 4, 5, and 9.

---

## 5. Exhaustive Literature Review

- **Multi-source + multi-query expansion** — MeSH terms, synonyms, drug brand/generic names.
- **Loop-until-dry retrieval** — keep expanding queries until no new relevant results surface.
- **Coverage critic pass** — a "what's missing?" agent (modality not searched, trial phase uncovered, source not queried).
- **Cross-source dedup** by DOI / PMID / NCT.
- **Stratification** by study type and recency.

---

## 6. App Integration (API-first; app deferred)

The **JSON API contract is the stable seam.** Any future app — the `qla.soahealthcare.net` portal, a new standalone web app, or the NeuroLogic WMA Electron app — consumes the same API. No app commitment now.

Endpoints (v1):
- `POST /analyze` — submit a case → returns a job id.
- `GET /report/{id}` — fetch structured result (decision-support + optional manuscript).
- `GET /report/{id}/export?format=pdf|html|json` — rendered exports.
- `GET /report/{id}/provenance` — the audit trail.

Auth, multi-user, and PHI-at-rest handling are stubbed with clean interfaces for a later hardening pass.

---

## 7. Guideline Strategy (v1 free → v2 licensed)

**Research finding (2026-06-04):** OpenEvidence obtains gated guidelines via **signed per-body licenses**, not scraping — NCCN (live Apr 2026), ASCO (May 2026); no ESMO deal exists. NCCN has no public API/pricing and requires a BD-negotiated license. They also treat guideline **decision-trees/figures as a distinct ingestion problem** from prose RAG.

**v1 — legally clean, free sources:**
- **NCI PDQ** — US-government, **public domain**, reuse-friendly, with a **syndication API**. Primary guideline backbone.
- **CC-BY open-access** ASCO/ESMO guideline papers (verify per-article license; CC BY-NC-ND does *not* permit commercial/derivative reuse).
- **PMC Open-Access subset** for full text.
- Open APIs: PubMed (E-utilities / existing MCP), **ClinicalTrials.gov**, **openFDA / DailyMed**, **NCI Clinical Trials Search API**.

**v2 — gated premium tier (deferred):**
- The `guidelines` connector exposes a clean interface so a **licensed NCCN source** drops in without touching `core/`.
- Action item when ready: NCCN business licensing (nccn.org/business-policy/licensing) — budget a months-long BD timeline; terms are private.
- Plan guideline figures/decision-trees as a separate ingestion concern.

---

## 8. Error Handling

- **Per-source graceful degradation** — one API down → mark source unavailable, continue with the rest, note it in the report.
- **Rate-limit backoff** on all connectors.
- **PHI detected post-scrub** → hard stop before any external call.
- **Citation verification failure** → drop the claim, log the rejection.
- **No results** → valid empty state, never fabricated.

---

## 9. Testing

- **Unit** — connectors (mocked HTTP), de-id scrubber (PHI fixtures), ID-validator (valid/invalid id sets), evidence grading.
- **Integration** — golden canned cases run end-to-end.
- **Anti-hallucination evals** (via the PAI `Evals` skill) — red-team cases with fabrication traps; assert **zero invented IDs** and **every claim traces to a verified source**. These are regression gates, run in CI.
- **Golden-case fixtures** for the CARE manuscript export.

---

## 10. Extensibility

Adding a new research vertical (e.g., cardiology, or Quran/Islamic-studies literature research) = create `verticals/<domain>/` with its own `search_strategies.yaml`, `schema.yaml`, and `templates/`. `core/` is never modified. This is the concrete payoff of the boundary rule in §2.

---

## 11. Phased Roadmap (input to /ultraplan)

- **Phase 0 — Skeleton & contracts:** repo scaffold, Pydantic schemas, API stubs, vertical config format, test harness.
- **Phase 1 — Connectors:** PubMed (reuse MCP/E-utilities), ClinicalTrials.gov, openFDA, PDQ guidelines. Each with ID-verification.
- **Phase 2 — Grounding & synthesis:** de-id scrubber, query expansion, retrieval, evidence grading, cite-or-omit, adversarial claim-checker.
- **Phase 3 — Output:** decision-support render + CARE manuscript template + PDF/HTML/JSON exports + provenance log.
- **Phase 4 — PAI skill wrapper:** thin `SKILL.md` shelling into the engine; voice notification + dated output like the digest skill.
- **Phase 5 — Hardening & evals:** anti-hallucination eval suite, error handling, rate limiting.
- **Phase 6 (future) — App + licensed guidelines:** consume the API in a chosen app; integrate licensed NCCN tier.

---

## 12. Open Risks

1. **Guideline licensing** — NCCN/ASCO/ESMO reuse needs signed licenses; v1 avoids this via public-domain PDQ + CC-BY OA. (Mitigated.)
2. **PHI handling** — de-id scrubber is a safety-critical component; must be tested adversarially and fail closed.
3. **Clinical liability** — framing as decision *support* with mandatory citations and human-in-the-loop; not autonomous advice.
4. **Guideline figures/decision-trees** — a distinct, harder ingestion problem deferred past v1.
