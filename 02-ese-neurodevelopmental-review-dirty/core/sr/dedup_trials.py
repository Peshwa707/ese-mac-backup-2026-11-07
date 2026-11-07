"""Trial-level deduplication: collapse multiple reports of one trial into one study.

A meta-analysis pools STUDIES (trials), not publications. One trial often has several
papers (primary results, follow-up, sub-analyses) reporting the SAME patients — pooling
each separately double-counts patients, over-weights the trial, and falsely narrows CIs.

Grouping is deterministic and CONSERVATIVE: merge only on an exact key (NCT first, then an
exact trial acronym). Records without a key stay as their own singleton trial — over-
splitting is far safer than over-merging (which would delete a real distinct trial).
Every group records its member PMIDs, key source, chosen anchor, and any cross-paper
value conflicts for human verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.sr.models import ExtractedStudy, ExtractedValue

# Trial-acronym shape: e.g. ACME-1, ALPHA-3, BETA-MM1, GAMMA-MM1.
# Suffix number is PART of the key, so ACME-1 != ACME-4 and ALPHA != ALPHA-3.
_ACRONYM_RE = re.compile(r"\b([A-Za-z]{3,}-(?:[A-Za-z]{1,4})?\d{1,2}[A-Za-z]?)\b")
_NCT_RE = re.compile(r"NCT\d{8}", re.I)
_SUBANALYSIS = (
    "pharmacokinetic", "population pharmaco", "poppk", "infection", "quality of life",
    "patient-reported", "patient reported", "post hoc", "post-hoc", "long-term follow",
    "health-related", "cost-effective", "real-world", "subgroup", "exposure-response",
    "exposure response", "correlative", "translational",
)
# Common non-trial tokens the acronym regex might catch — never use as a trial key.
_ACRONYM_STOP = {"covid-19", "il-6", "pd-1", "pd-l1", "cd3-1", "bcl-2", "gprc5d-1"}


def _real(v: ExtractedValue) -> bool:
    return v.flag != "NR" and (v.value or "").strip().upper() != "NR"


def trial_acronym(text: str) -> str | None:
    for m in _ACRONYM_RE.finditer(text or ""):
        tok = m.group(1)
        if tok.lower() in _ACRONYM_STOP:
            continue
        if not any(c.isupper() for c in tok):       # require some uppercase (trial-like)
            continue
        return tok.upper().replace(" ", "-")
    return None


def trial_key(pmid: str, record: dict, nct_by_pmid: dict[str, str]) -> tuple[str | None, str]:
    """Return (key, source). NCT first, then exact acronym, else None (singleton)."""
    nct = (nct_by_pmid.get(pmid) or "").split(",")[0].strip().upper()
    if not nct:
        m = _NCT_RE.search(((record.get("title") or "") + " " + (record.get("abstract") or "")))
        nct = m.group(0).upper() if m else ""
    if nct:
        return nct, "nct"
    acr = trial_acronym((record.get("title") or "") + " " + (record.get("abstract") or ""))
    if acr:
        return acr, "acronym"
    return None, "singleton"


@dataclass
class TrialGroup:
    trial_id: str
    key_source: str
    member_pmids: list[str]
    anchor_pmid: str
    extraction: ExtractedStudy
    conflicts: list[str] = field(default_factory=list)


def _score(es: ExtractedStudy, record: dict) -> tuple[int, int]:
    """Anchor score: more real values is better; sub-analysis titles are down-ranked."""
    reals = sum(1 for v in es.values if _real(v))
    title = (record.get("title") or "").lower()
    penalty = 0 if not any(k in title for k in _SUBANALYSIS) else 1
    return (-penalty, reals)   # sort desc: prefer non-sub-analysis, then most data


def merge_trial(members: list[str], ext_by_pmid: dict[str, ExtractedStudy],
                records: dict[str, dict], key: str, source: str) -> TrialGroup:
    anchor_pmid = max(members, key=lambda p: _score(ext_by_pmid[p], records.get(p, {})))
    merged = ext_by_pmid[anchor_pmid].model_copy(deep=True)
    by_field = {(v.group, v.field): v for v in merged.values}
    conflicts: list[str] = []
    for p in members:
        if p == anchor_pmid:
            continue
        for v in ext_by_pmid[p].values:
            cur = by_field.get((v.group, v.field))
            if cur is None:
                merged.values.append(v.model_copy(deep=True)); by_field[(v.group, v.field)] = v
            elif not _real(cur) and _real(v):              # fill an NR gap from a sibling
                cur.value, cur.flag, cur.quote = v.value, v.flag, v.quote
                merged.extraction_notes = (merged.extraction_notes + f" [{v.field} from {p}]").strip()
            elif _real(cur) and _real(v) and cur.value.strip() != v.value.strip():
                conflicts.append(f"{v.field}: anchor={cur.value!r} vs {p}={v.value!r}")
    merged.trial_id, merged.key_source = key, source
    merged.member_pmids = list(members)
    if conflicts:
        merged.extraction_notes = (merged.extraction_notes + " CONFLICTS: " + "; ".join(conflicts)).strip()
    return TrialGroup(key, source, list(members), anchor_pmid, merged, conflicts)


def group_by_trial(extractions: list[ExtractedStudy], records: dict[str, dict],
                   nct_by_pmid: dict[str, str]) -> list[TrialGroup]:
    """Cluster reports into trials. Keyless records become singleton trials (never merged)."""
    ext_by_pmid = {es.pmid: es for es in extractions}
    clusters: dict[str, list[str]] = {}
    meta: dict[str, str] = {}
    singleton = 0
    for es in extractions:
        key, source = trial_key(es.pmid, records.get(es.pmid, {}), nct_by_pmid)
        if key is None:
            singleton += 1
            key = f"__singleton_{es.pmid}"
            source = "singleton"
        clusters.setdefault(key, []); clusters[key].append(es.pmid); meta[key] = source
    return [merge_trial(members, ext_by_pmid, records, key, meta[key])
            for key, members in clusters.items()]


def dedup_summary(groups: list[TrialGroup], n_reports: int) -> dict:
    from collections import Counter
    by_source = Counter(g.key_source for g in groups)
    return {
        "reports": n_reports,
        "trials": len(groups),
        "by_key_source": dict(by_source),
        "multi_report_trials": sum(1 for g in groups if len(g.member_pmids) > 1),
        "trials_with_conflicts": sum(1 for g in groups if g.conflicts),
    }
