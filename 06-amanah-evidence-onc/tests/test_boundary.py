"""Enforce the inviolable boundary rule: no domain (oncology) knowledge in core/.

We scan for clinical domain terms that would only appear if someone hardcoded
oncology knowledge into the engine. Data-source / architectural words (PubMed,
PDQ, NCI, guideline) are allowed — they name sources, not domain facts. The
words 'oncology' / 'cancer' are also allowed because they appear in comments
that *describe* this very rule.
"""

from pathlib import Path

_CORE = Path(__file__).resolve().parents[1] / "core"

# Clinical domain values + vertical-specific artifacts that must live in
# verticals/, never in core/. Each entry would catch a real regression of the
# boundary fixes (hardcoded template names, the pdq ref-list key, chemo drugs).
_DENYLIST = [
    # Clinical domain values.
    "nsclc",
    "egfr",
    "ecog",
    "carcinoma",
    "chemotherapy",
    "tumor",
    "biomarker",
    "metasta",
    "osimertinib",
    "pembrolizumab",
    "nivolumab",
    "carboplatin",
    "pemetrexed",
    "trastuzumab",
    "myeloma",
    "rrmm",
    "bcma",
    "car-t",
    "car t",
    "bsab",
    "bispecific",
    # Named trial-programme acronyms must not leak into domain-agnostic core/.
    "cartitude",
    "majestec",
    "karmma",
    "magnetismm",
    # Vertical-specific artifacts that previously leaked into core/.
    "pdq_summaries",
    "care_report.html",
    "decision_support.html",  # the model FIELD is decision_support (no .html)
    "care-guideline",
    # LLM prompt framing moved to the vertical (Phase 5 Wave B, H1/H2). These
    # exact phrases lived in hardcoded core/ system prompts and now live only in
    # verticals/oncology/schema.yaml. Generic words (clinical, decision support)
    # are deliberately NOT listed — they appear in legitimate neutral comments
    # and would cause false positives.
    "clinical data extractor",  # was structure.py's system prompt
    "clinician",                # was synthesis prompt + pipeline disclaimer
    "care report",              # was the manuscript/CARE framing
]


def test_core_has_no_hardcoded_domain_terms():
    offenders = []
    for path in _CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for term in _DENYLIST:
            if term in text:
                offenders.append(f"{path.relative_to(_CORE.parent)}: '{term}'")
    assert not offenders, "domain terms leaked into core/: " + "; ".join(offenders)
