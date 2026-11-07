"""PROSPERO registration draft + best-effort duplicate check (protocol Steps 1-2).

The registration draft populates the content fields from the vertical config and
the live run; administrative fields (investigators, dates, funding) are left as
explicit [HUMAN] placeholders for the submitter. The duplicate check is
best-effort over the public PROSPERO site and degrades to an honest "search
manually" note rather than asserting "none found" it cannot verify.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from core.sr.config import SRConfig
from core.sr.models import SRRun

_HUMAN = "[HUMAN — to complete before submission]"
_PROSPERO_SEARCH = "https://www.crd.york.ac.uk/prospero/search"


def registration_fields(cfg: SRConfig, run: SRRun) -> list[tuple[str, str]]:
    """The 22 core PROSPERO fields as (label, value) — content filled, admin [HUMAN]."""
    pico = cfg.pico.get("pico", {})
    review = cfg.pico.get("review", {})
    outcomes = pico.get("outcomes", {})
    primary = "; ".join(outcomes.get("primary", []))
    secondary = "; ".join(outcomes.get("secondary", []))
    dbs = "PubMed/MEDLINE (executed); Embase, CENTRAL, Web of Science (strings produced); " \
          "ClinicalTrials.gov; conference abstracts — see search strategy."
    # Field 13/17 content is vertical-supplied; fall back to the PICO population /
    # eligibility text so no domain string is hardcoded in the engine.
    population_txt = " ".join((pico.get("population") or "").split())
    condition = " ".join((review.get("condition") or population_txt).split())
    study_types = " ".join((review.get("study_types") or "").split()) or (
        "Study designs eligible per the protocol's inclusion criteria; "
        + "; ".join(cfg.inclusion_criteria[:2]))
    synth_groups = (" overall and by " + cfg.subgroup_field) if cfg.synthesis_arms else " overall"
    return [
        ("1. Review title", cfg.review_title),
        ("2. Original language title", cfg.review_title),
        ("3. Anticipated or actual start date", _HUMAN),
        ("4. Anticipated completion date", _HUMAN),
        ("5. Stage of review at time of submission", _HUMAN),
        ("6. Named contact", _HUMAN),
        ("7. Named contact email / affiliation", _HUMAN),
        ("8. Review team members & affiliations", _HUMAN),
        ("9. Funding sources / sponsors", _HUMAN),
        ("10. Conflicts of interest", _HUMAN),
        ("11. Review question", cfg.pico_text()),
        ("12. Searches", dbs),
        ("13. Condition or domain being studied", condition),
        ("14. Population", population_txt),
        ("15. Intervention(s) / exposure(s)", " ".join((pico.get("intervention") or "").split())),
        ("16. Comparator(s) / control", " ".join((pico.get("comparator") or "").split())),
        ("17. Types of study to be included", study_types),
        ("18. Primary outcome(s)", primary),
        ("19. Secondary outcome(s)", secondary),
        ("20. Data extraction (selection and coding)",
         "Dual review (AI reviewer model(s) + independent human verification by a "
         "reviewer-of-record); structured data dictionary; abstract- and full-text-level; "
         "values verbatim or NR/ESTIMATED with source quotes."),
        ("21. Risk of bias (quality) assessment",
         "Cochrane RoB 2.0 (RCTs), ROBINS-I (non-randomized), Newcastle-Ottawa/JBI (single-arm)."),
        ("22. Strategy for data synthesis",
         f"Random-effects pooling (proportions via Freeman-Tukey/REML; effect sizes via "
         f"inverse-variance){synth_groups}; heterogeneity by I²; GRADE certainty; "
         f"Bayesian NMA if shared comparators permit."),
    ]


@dataclass
class DuplicateCheck:
    status: str                       # "checked" | "manual"
    query: str = ""
    note: str = ""
    titles: list[str] = field(default_factory=list)


async def duplicate_check(query: str, client: httpx.AsyncClient | None = None) -> DuplicateCheck:
    """Best-effort PROSPERO duplicate search. Honest fallback if it can't be run."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    try:
        resp = await client.get(_PROSPERO_SEARCH, params={"searchid": "", "page": "1", "searchterm": query})
        if resp.status_code != 200 or "prospero" not in resp.text.lower():
            return DuplicateCheck("manual", query,
                                  "PROSPERO could not be auto-searched; search the registry manually.")
        import re
        titles = re.findall(r"<a[^>]*recordid[^>]*>(.*?)</a>", resp.text, re.I | re.S)
        titles = [re.sub(r"<[^>]+>", "", t).strip() for t in titles][:20]
        return DuplicateCheck("checked", query,
                              "Best-effort automated search; confirm completeness manually.", titles)
    except Exception as exc:
        return DuplicateCheck("manual", query, f"Auto-search failed ({exc}); search PROSPERO manually.")
    finally:
        if own:
            await client.aclose()
