"""Reconcile two independent screeners (Claude R1 vs Codex R2).

- Cohen's kappa (3-category: include/exclude/uncertain)
- Consensus rule (protocol 2.5): a record advances to full-text if EITHER reviewer
  says include OR uncertain; excluded only if BOTH exclude.
- Disagreement list (the records a human adjudicates).
"""
import json, sys
from collections import Counter


def cohen_kappa(a, b):
    cats = ["include", "exclude", "uncertain"]
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in cats)
    kappa = (po - pe) / (1 - pe) if pe != 1 else 1.0
    return po, pe, kappa


def reconcile(r1_map, r2_map):
    pmids = sorted(set(r1_map) | set(r2_map))
    a = [r1_map.get(p, "uncertain") for p in pmids]
    b = [r2_map.get(p, "uncertain") for p in pmids]
    po, pe, kappa = cohen_kappa(a, b)
    advance, exclude, disagree = [], [], []
    for p in pmids:
        d1, d2 = r1_map.get(p, "uncertain"), r2_map.get(p, "uncertain")
        if d1 != d2:
            disagree.append((p, d1, d2))
        # union of include/uncertain advances; both-exclude drops
        if d1 == "exclude" and d2 == "exclude":
            exclude.append(p)
        else:
            advance.append(p)
    return {
        "n": len(pmids), "raw_agreement": round(po, 4), "expected": round(pe, 4),
        "cohen_kappa": round(kappa, 4),
        "advance_to_fulltext": advance, "excluded_at_title_abstract": exclude,
        "disagreements": disagree,
    }


def landis_koch(k):
    if k < 0: return "poor"
    if k <= .20: return "slight"
    if k <= .40: return "fair"
    if k <= .60: return "moderate"
    if k <= .80: return "substantial"
    return "almost perfect"


if __name__ == "__main__":
    r1 = json.load(open(sys.argv[1]))   # {pmid: decision}
    r2 = json.load(open(sys.argv[2]))
    out = reconcile(r1, r2)
    out["kappa_interpretation"] = landis_koch(out["cohen_kappa"])
    print(json.dumps(out, indent=2))
