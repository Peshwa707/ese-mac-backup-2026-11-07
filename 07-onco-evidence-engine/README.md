# Onco-Evidence Engine

A grounded clinical-evidence engine. From a single literature-grounded analysis
of a **de-identified** oncology case it produces (1) citation-grounded clinical
**decision support** and (2) a CARE-guideline **case-report** draft.

## Systematic-review platform — moved

**Canonical SR + stats stack:** [`evidence-synthesis-engine`](https://github.com/Peshwa707/evidence-synthesis-engine) (`~/Projects/evidence-synthesis-engine`).

Use the **EvidenceSynthesis** skill and `ese` CLI there. This repo keeps the **case-analysis** web app and oncology vertical data; do not maintain two SR codebases. See `docs/MIGRATION.md` in the new repo.

The `core/sr/` tree here is **legacy** — highlights of what was salvaged:

A multi-database, multi-method, multi-domain evidence-synthesis platform built on
the same grounded core. Highlights:

- **Review types:** systematic · scoping (PRISMA-ScR) · rapid · living (`--profile`).
- **Sources:** PubMed, Europe PMC, ClinicalTrials.gov (live, PMID/DOI/NCT-verified) +
  a generic **import connector** for paywalled CENTRAL/Embase/Web of Science exports
  (RIS/NBIB/CSV, provenance-attested) + backward/forward citation mining.
- **Pipeline:** multi-source grounding → AI Reviewer-1 screening → full text
  (PMC/Unpaywall/user PDFs) → extraction → RoB (RoB2/ROBINS-I/NOS) → GRADE →
  meta-analysis (pooled proportions + hazard ratios via R/metafor; Bayesian NMA via
  gemtc, honest-gated) → PROSPERO draft → citation-checked narrative.
- **Output:** per-component Markdown/DOCX/PDF manuscript + a **RAISE / PRISMA-trAIce**
  AI-use statement, per-vertical output homes.
- **Honesty:** every PMID/DOI live-verified (or import provenance-attested); values
  abstract/full-text-anchored or NR/ESTIMATED; nothing fabricated. AI = Reviewer 1;
  human Reviewer 2 is a required gate.

Run via the `SystematicReview` skill or `python -m core.sr.manuscript runs/<id>`.
A non-oncology vertical (`verticals/heart-failure-sglt2/`) validates the
domain-agnostic core (zero `core/` changes for a new domain).

**Design stance:** oncology-first in behavior, generic-by-construction in
architecture. The reusable engine lives in `core/` and is domain-agnostic; all
oncology specifics live in `verticals/oncology/` as YAML data + Jinja templates.
Adding a new research domain = a new `verticals/<x>/` folder, zero `core/`
changes. See `docs/specs/2026-06-04-onco-evidence-engine-design.md`.

## Layout

```
core/          domain-agnostic engine
  connectors/  pubmed · clinicaltrials · openfda · guidelines(PDQ)
  grounding/   deid (fail-closed) · structure · query expansion · retrieval · verify
  synthesis/   grading · conflict · synthesize (retrieval-only) · adversarial claim-check
  render/      template → HTML/PDF/JSON · provenance log
  llm/         Anthropic wrapper
  api/         FastAPI contract
verticals/oncology/   schema.yaml · search_strategies.yaml · guideline_refs.yaml · templates/
tests/
```

## Anti-hallucination safeguards (defense-in-depth)

Retrieval-only grounding · live ID-verification round-trips · cite-or-omit ·
quote-anchoring · adversarial claim-checker · empty=empty · conflict surfacing ·
evidence grade + confidence on every claim · per-report provenance log ·
human-in-the-loop decision-support framing.

## Setup

```bash
pip install -e ".[dev]"
```

Environment variables:

| Var | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Required for the LLM stages (structure, synthesis, conflict, claim-check). |
| `NCBI_API_KEY` | Optional — higher PubMed E-utilities rate limit. |
| `ONCO_VERTICAL` | Active vertical (default `oncology`). |
| `ONCO_PROVENANCE_DIR` | Where provenance JSON is written (default `/tmp/onco-provenance`). |

## Run the API

```bash
uvicorn core.api.app:app --reload
# POST /analyze {"case_text": "..."}              -> {"job_id": "..."}
# GET  /report/{id}                                -> status + report
# GET  /report/{id}/export?format=pdf|html|json    -> rendered export
# GET  /report/{id}/provenance                     -> audit trail
```

PDF export uses Chrome headless; if no Chrome/Chromium binary is present, use
`format=html` or `format=json` (the API returns 503 for PDF in that case).

## Run the web app

A standalone web UI (vanilla JS, no build step) over the same API:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # needed for analysis
uvicorn web.server:app --reload --port 8000
# open http://localhost:8000
```

Paste a de-identified case, optionally toggle the CARE manuscript, and submit.
Findings render as cards with color-coded evidence grades and **verified-citation
chips** linking to the source (PMID/NCT); exports and the provenance trail are one
click away. If `ONCO_API_TOKEN` is set, enter it under *Settings* (stored in the
browser's localStorage, sent as a bearer token); unset = open dev mode.

## Tests

```bash
pytest                 # unit suite — runs fully offline (HTTP mocked)
pytest -m live         # live end-to-end (needs network + ANTHROPIC_API_KEY)
```

## Status

This is the Phase 0–3 vertical slice (skeleton → connectors → grounding/synthesis
→ output). Deferred: PAI skill wrapper, anti-hallucination eval suite + hardening,
consumer app, and the licensed-guideline (e.g. NCCN) tier behind the existing
`guidelines` connector seam.
