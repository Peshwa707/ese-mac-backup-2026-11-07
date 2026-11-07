"""RAISE / PRISMA-trAIce AI-use reporting (2025 standards).

Auto-populates a transparent statement of how AI was used in the review, from the
versioned stage specs + the run's honesty audit. This is what distinguishes a
defensible AI-assisted review from "an LLM wrote my review": tool, role, model,
validation evidence, human oversight, and reproducibility — all reported, not asserted.

References: Cochrane/Campbell/JBI/CEE 2025 position statement (RAISE);
PRISMA-trAIce checklist (2025).
"""

from __future__ import annotations

# Per-stage AI use. Deterministic stages are reported as NOT AI (important for
# transparency: search/verification/GRADE/meta are rule-based, not model output).
# AI stages carry a `tag` matching the runtime model-provenance recorder
# (core.llm.client.stage_log); the actual model/backend is filled in at build time
# from what RAN — never asserted from a hardcoded string.
STAGES = [
    {"stage": "Literature search & PMID verification", "ai": "No", "tag": "",
     "model": "—", "role": "NCBI E-utilities (deterministic); every PMID round-trip-verified."},
    {"stage": "Title/abstract screening", "ai": "Yes", "tag": "screening",
     "model": "", "role": "Reviewer 1 include/exclude/uncertain; schema-validated + repaired."},
    {"stage": "Full-text retrieval", "ai": "No", "tag": "",
     "model": "—", "role": "PMC + Unpaywall + user PDFs (deterministic)."},
    {"stage": "Data extraction", "ai": "Yes", "tag": "extraction",
     "model": "", "role": "Abstract/full-text extraction; values NR/ESTIMATED + verbatim quotes."},
    {"stage": "Risk of bias", "ai": "Yes", "tag": "rob",
     "model": "", "role": "RoB2/ROBINS-I/NOS per-domain judgments + rationale + quote."},
    {"stage": "GRADE certainty", "ai": "No", "tag": "",
     "model": "—", "role": "Deterministic rule-based downgrades (reproducible)."},
    {"stage": "Meta-analysis", "ai": "No", "tag": "",
     "model": "—", "role": "R metafor (Freeman-Tukey, REML) — deterministic."},
    {"stage": "Narrative prose", "ai": "Yes", "tag": "synthesis",
     "model": "", "role": "Drafted from a grounded brief, then adversarially citation-checked."},
]


def _stage_log_snapshot() -> list[dict]:
    from core.llm import client
    return client.stage_log()


def _resolve_stage_models(stage_models: list[dict] | None) -> tuple[list[dict], bool]:
    """Fill each AI stage's model from the runtime stage log; flag if any stage ran
    on a local/cheaper backend so the statement can call for extra review."""
    from core.llm import client

    log = stage_models if stage_models is not None else client.stage_log()
    by_tag = {e.get("stage"): e for e in log}
    any_local = False
    out = []
    for s in STAGES:
        row = dict(s)
        rec = by_tag.get(s.get("tag"))
        if s["ai"] == "Yes":
            if rec:
                backend = rec.get("backend", "anthropic")
                row["model"] = f'{rec.get("model", "?")} ({backend}, {rec.get("n_calls", "?")} calls)'
                if backend not in {"anthropic", "harness"}:
                    any_local = True
            else:
                row["model"] = f"{client.current_model()} (engine default; not recorded this run)"
        out.append(row)
    return out, any_local


def build_ai_reporting(run, audit: dict | None, profile, day: str,
                       stage_models: list[dict] | None = None) -> dict:
    """Structured RAISE / PRISMA-trAIce statement for rendering."""
    stages, any_local = _resolve_stage_models(stage_models)
    anchor = (audit or {}).get("anchoring", {})
    res = (audit or {}).get("resolution", {})
    validation = [
        f"Citation integrity: {res.get('resolved', '—')}/{res.get('checked', '—')} included "
        f"PMIDs re-resolved live against PubMed.",
        f"Extraction grounding: {anchor.get('anchored', '—')}/{anchor.get('substantive_values', '—')} "
        f"substantive values quote-anchored to source "
        f"({round(100 * anchor.get('anchored_rate', 0), 1)}%).",
        f"{anchor.get('estimated_for_human_review', '—')} values flagged ESTIMATED for human review; "
        f"{anchor.get('nr', '—')} honestly NR (not reported).",
        "Narrative prose dropped automatically if it cites any unverified PMID.",
    ]
    signed = getattr(run, "reviewer_signoff", None)
    oversight = ("AI acted as Reviewer 1 only. Independent human Reviewer 2 verification and "
                 "clinical sign-off are mandatory gates; all AI output is a draft. ")
    oversight += (f"Reviewer-2 sign-off RECORDED by {signed.reviewer} ({signed.timestamp}); "
                  "conclusions unlocked." if signed else
                  "No Reviewer-2 sign-off yet — final conclusions and GRADE certainty are "
                  "withheld as DRAFT (hard lock).")
    if any_local:
        oversight += (" NOTE: one or more stages ran on a local/cheaper model — those outputs "
                      "warrant an elevated human-review rate.")
    return {
        "tools": "Retrieval-grounded LLM stages with cite-or-omit; no fabricated content. "
                 "Models used per stage are listed below (recorded at runtime).",
        "dates": day,
        "review_type": f"{profile.label} ({profile.reporting}).",
        "stages": stages,
        "stage_models": stage_models if stage_models is not None else _stage_log_snapshot(),
        "human_oversight": oversight,
        "reproducibility": "Prompts, output schemas, and model selections are versioned in the "
                           f"repository; full provenance log id {run.provenance_id[:8]}. Deterministic "
                           "stages (search, GRADE, meta-analysis) reproduce exactly.",
        "validation": validation,
        "limitations": "LLM stages have run-to-run variance; extraction is abstract-level where no "
                       "full text was obtainable; not all databases executed (see search appendix).",
    }
