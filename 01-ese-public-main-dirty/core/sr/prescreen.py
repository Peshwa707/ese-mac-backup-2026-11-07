"""Deterministic publication-type pre-screen filter (WP3).

Before LLM title/abstract screening, route records by publication type:
  - exclude_by_type   : editorials/letters/comments/news/errata/notes/reviews
                        (protocol 2.2.3) — unless the record is also a trial.
  - conference_abstract: conference abstracts/papers -> abstract-only sensitivity
                        bucket (protocol 2.2.4), not the primary synthesis.
  - screen            : everything else (trials, articles, unknown) -> normal screening.

Type strings follow PubMed PublicationType and Embase M3 values; matching is
case-insensitive substring so combined tags ("Letter; Clinical Trial") are handled.
A blank/unknown type is NEVER excluded — it always proceeds to screening.
"""
from __future__ import annotations

PRESCREEN_BUCKETS = ("exclude_by_type", "conference_abstract", "screen")

# Substrings that, alone, make a record ineligible by type.
_EXCLUDE_TYPES = (
    "editorial", "letter", "comment", "news", "erratum", "note",
    "review", "short survey", "biography", "interview", "retraction",
    "newspaper", "address", "autobiography",
)
# Conference records -> sensitivity bucket.
_CONFERENCE_TYPES = ("conference abstract", "conference paper", "conference review", "congress")
# Trial/primary markers that OVERRIDE an exclude classification.
_TRIAL_OVERRIDE = ("trial", "randomized", "randomised")


def _bucket_for(study_type: str | None) -> str:
    t = (study_type or "").lower()
    if not t.strip():
        return "screen"
    # Conference abstracts route to the sensitivity bucket FIRST — even a conference
    # abstract *of* a trial ("Conference Abstract; Clinical Trial") is abstract-only
    # data (protocol 2.2.4), so this must precede the trial override.
    if any(k in t for k in _CONFERENCE_TYPES):
        return "conference_abstract"
    # Trial marker overrides exclude-by-type (e.g. "Letter; Clinical Trial" -> screen).
    if any(k in t for k in _TRIAL_OVERRIDE):
        return "screen"
    if any(k in t for k in _EXCLUDE_TYPES):
        return "exclude_by_type"
    return "screen"


def _field(rec, name: str):
    """Read a field from a dict record OR a Citation-like object."""
    if isinstance(rec, dict):
        return rec.get(name)
    return getattr(rec, name, None)


def _pid(rec) -> str:
    return str(_field(rec, "pmid") or _field(rec, "id") or "")


def classify_by_type(records: list) -> dict[str, dict]:
    """Map each record (dict or Citation, with 'pmid'/'id' + 'study_type') to its bucket.

    Returns {pmid: {"bucket": <one of PRESCREEN_BUCKETS>, "study_type": <raw>}}.
    """
    out: dict[str, dict] = {}
    for r in records:
        st = _field(r, "study_type") or ""
        out[_pid(r)] = {"bucket": _bucket_for(st), "study_type": st}
    return out


def prescreen_records(records: list):
    """Partition records BEFORE LLM screening.

    Returns ``(to_screen, report)`` where ``to_screen`` keeps only records whose
    type warrants title/abstract screening, and ``report`` records what was
    deterministically routed away (for PRISMA + the sensitivity bucket):
        {"counts": {bucket: n}, "exclude_by_type": [pmid...], "conference_abstract": [pmid...]}
    Records are otherwise returned in their original form (dict or Citation).
    """
    cls = classify_by_type(records)
    to_screen, excluded, conference = [], [], []
    for r in records:
        bucket = cls[_pid(r)]["bucket"]
        if bucket == "exclude_by_type":
            excluded.append(_pid(r))
        elif bucket == "conference_abstract":
            conference.append(_pid(r))
        else:
            to_screen.append(r)
    report = {
        "counts": {"screen": len(to_screen),
                   "exclude_by_type": len(excluded),
                   "conference_abstract": len(conference)},
        "exclude_by_type": excluded,
        "conference_abstract": conference,
    }
    return to_screen, report


def summarize(classified: dict[str, dict]) -> dict[str, int]:
    """Counts per bucket, for PRISMA reporting."""
    counts = {b: 0 for b in PRESCREEN_BUCKETS}
    for v in classified.values():
        counts[v["bucket"]] = counts.get(v["bucket"], 0) + 1
    return counts


def load_prescreen_report(run_dir) -> dict:
    """Read the persisted prescreen.json written during screening (empty dict if absent).

    Used by run-folder/manuscript rebuild paths so build_run gets the prescreen report
    and PRISMA 'screened' counts stay consistent with what was actually screened.
    """
    import json
    from pathlib import Path
    p = Path(run_dir) / "prescreen.json"
    if not p.exists():
        return {}   # legitimately no pre-screen ran for this run
    # Present-but-invalid is a corrupt source-of-truth: fail CLOSED rather than
    # silently fabricate "no prescreen" (which would over-report counts.screened).
    report = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"corrupt prescreen.json (not an object): {p}")
    counts = report.get("counts")
    if not isinstance(counts, dict):
        raise ValueError(f"corrupt prescreen.json (missing/invalid 'counts'): {p}")
    # All three buckets must be present as ints, else build_run would fall back to
    # len(verified) for the missing 'screen' count and re-open the over-report path.
    for key in PRESCREEN_BUCKETS:  # screen, exclude_by_type, conference_abstract
        v = counts.get(key)
        # bool is an int subclass in Python — reject it explicitly so "screen": true
        # cannot masquerade as a valid count.
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"corrupt prescreen.json (counts['{key}'] missing/non-int): {p}")
    return report
