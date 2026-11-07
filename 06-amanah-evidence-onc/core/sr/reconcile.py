"""Reconcile two independent screeners — Cohen's kappa + consensus (WP6).

Quantifies between-reviewer agreement (nominal AND ordinal/weighted kappa, because
exclude<->uncertain is an *adjacent* disagreement, not a true conflict) and applies
the conservative PRISMA consensus: a record advances to full-text if EITHER reviewer
judged it include or uncertain; it is excluded only when BOTH reviewers exclude.
Pure Python, no dependencies.
"""
from __future__ import annotations

from collections import Counter

# Ordinal scale (best -> worst for advancing): include > uncertain > exclude.
ORDER = ["include", "uncertain", "exclude"]
_ADVANCE = {"include", "uncertain"}


def _dec(x) -> str:
    return (getattr(x, "value", x) or "").lower()


def cohen_kappa(a: list, b: list, weights: str | None = None, order: list | None = None) -> float:
    """Cohen's kappa between paired rater decisions ``a`` and ``b``.

    ``weights``: None (nominal), 'linear', or 'quadratic' (ordinal — partial credit
    for adjacent disagreements, using ``order``). Returns 1.0 for perfect agreement,
    0.0 for chance-level, and is defined as (po-pe)/(1-pe).
    """
    a = [_dec(x) for x in a]
    b = [_dec(x) for x in b]
    n = len(a)
    if n == 0 or len(b) != n:
        return 0.0
    if order is not None:
        cats = order
    elif weights is None:
        cats = sorted(set(a) | set(b))   # nominal: ordering is irrelevant
    else:
        # Weighted/ordinal needs a real scale — never silently use alphabetical order.
        present = set(a) | set(b)
        if present <= set(ORDER):
            cats = ORDER                 # default to the screening scale
        else:
            raise ValueError(
                "weighted cohen_kappa requires an explicit 'order' for non-standard "
                f"categories: {sorted(present - set(ORDER))}")
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)

    def w(i: int, j: int) -> float:
        if weights is None:
            return 1.0 if i == j else 0.0
        d = abs(i - j) / (k - 1) if k > 1 else 0.0
        return 1.0 - (d * d if weights == "quadratic" else d)

    ca, cb = Counter(a), Counter(b)
    po = pe = 0.0
    for x, y in zip(a, b):
        po += w(idx[x], idx[y])
    po /= n
    for ci in cats:
        for cj in cats:
            pe += w(idx[ci], idx[cj]) * (ca[ci] / n) * (cb[cj] / n)
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def reconcile(r1: dict, r2: dict) -> dict:
    """Reconcile two reviewers' decisions keyed by record id.

    ``r1``/``r2`` map id -> decision (include/exclude/uncertain). Missing ids default
    to 'uncertain' (conservative). Returns nominal + weighted kappa, the consensus
    advance/exclude partitions (union rule), and the disagreement list.
    """
    ids = sorted(set(r1) | set(r2))
    a = [_dec(r1.get(i, "uncertain")) for i in ids]
    b = [_dec(r2.get(i, "uncertain")) for i in ids]
    advance, exclude_both, disagreements = [], [], []
    for i in ids:
        d1, d2 = _dec(r1.get(i, "uncertain")), _dec(r2.get(i, "uncertain"))
        if d1 != d2:
            disagreements.append({"id": i, "r1": d1, "r2": d2,
                                  "hard": {d1, d2} == {"include", "exclude"}})
        if d1 == "exclude" and d2 == "exclude":
            exclude_both.append(i)
        elif d1 in _ADVANCE or d2 in _ADVANCE:   # union: either include/uncertain advances
            advance.append(i)
        else:
            advance.append(i)  # any non-(both-exclude) is conservatively advanced
    return {
        "n": len(ids),
        "kappa_nominal": round(cohen_kappa(a, b), 4),
        "kappa_weighted": round(cohen_kappa(a, b, weights="linear", order=ORDER), 4),
        "advance": advance,
        "exclude_both": exclude_both,
        "disagreements": disagreements,
        "n_hard_conflicts": sum(1 for d in disagreements if d["hard"]),
    }
