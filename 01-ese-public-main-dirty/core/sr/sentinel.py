"""Named-trial sentinel — a positive control for screening (WP4).

Configured ``named_trials`` (the pivotal studies a review MUST capture) are checked
after screening: each must have at least one matching record that reached the
required stage (advanced to full-text). If a named trial is excluded — or never
retrieved — the run records a violation and, under ``sentinel_strict``, fails loudly.

This is the safeguard for the near-miss where flawed eligibility wording silently
excluded a pivotal trial. Matching is over title AND abstract (trial programme names
are frequently absent from the article title).
"""
from __future__ import annotations

import re

# Decisions that count as "advanced to full-text" (the conservative bar).
_ADVANCE = {"include", "uncertain"}


class SentinelViolation(RuntimeError):
    """Raised (under sentinel_strict) when a configured named trial fails screening."""


def _trial_pattern(name: str) -> "re.Pattern":
    """Compile a boundary-aware matcher for a trial name.

    Segments are split on non-alphanumerics and rejoined with a flexible separator,
    so "ACME-3" matches "acme-3" or "acme 3". Crucially, a name WITHOUT a trailing
    number (e.g. "ACME") must NOT match a numbered variant ("ACME-3"), otherwise a
    configured trial could be falsely satisfied by a different trial whose name shares
    its prefix. Names ending in a number match that exact number only.
    """
    segs = [re.escape(s) for s in re.split(r"[^a-z0-9]+", name.lower()) if s]
    core = r"[^a-z0-9]?".join(segs)
    # Hyphen-aware boundaries on BOTH sides so a base trial never matches a hyphen-
    # prefixed or hyphen-suffixed variant: "ACME" must not match "X-ACME", and
    # "ACME" must not match "ACME-3" (the lookbehind/ahead reject a '-' neighbour).
    pat = r"(?<![a-z0-9-])" + core + r"(?![a-z0-9-])"
    if not name.rstrip()[-1:].isdigit():
        # Also block spaced numbered variants "<name><sep...><n>" ("ACME - 3", "ACME 3").
        # The digit must follow IMMEDIATELY after the separator run, so a genuine
        # "the ACME trial" still matches; sentinel false-negatives fail safe anyway.
        pat += r"(?![^a-z0-9]*\d)"
    return re.compile(pat)


def _decision_str(d) -> str:
    # accept enum or str
    return getattr(d, "value", d) if d is not None else ""


def check_named_trials(named: list[str], screened: list[dict], min_stage: str = "advance") -> dict:
    """Verify each named trial survived screening.

    ``screened``: list of {pmid, title, abstract?, decision}. ``min_stage``:
    "advance" (decision in include/uncertain) or "include".
    Returns {passed: bool, violations: [trial...], matched: {trial: stage}}.
    A trial violates if NO matching record reached the required stage (covers both
    "excluded" and "never retrieved").
    """
    accept = _ADVANCE if min_stage == "advance" else {"include"}
    violations: list[str] = []
    matched: dict[str, str] = {}
    for trial in named:
        pat = _trial_pattern(trial)
        best = None  # best stage seen for any matching record
        for rec in screened:
            hay = ((rec.get("title") or "") + " " + (rec.get("abstract") or "")).lower()
            if trial.strip() and pat.search(hay):
                dec = _decision_str(rec.get("decision")).lower()
                if dec in accept:
                    best = "advanced" if dec in _ADVANCE else dec
                    break
                best = best or f"matched-but-{dec or 'no-decision'}"
        if best and best in ("advanced", "include"):
            matched[trial] = best
        else:
            violations.append(trial)
            if best:
                matched[trial] = best  # e.g. "matched-but-exclude" / "not-found" below
            else:
                matched[trial] = "not-found"
    return {"passed": not violations, "violations": violations, "matched": matched}
