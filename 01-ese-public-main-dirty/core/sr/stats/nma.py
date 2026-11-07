"""Bayesian network meta-analysis (NMA) — harness + honest feasibility gate.

An NMA can only compare two treatment arms indirectly if studies share a common
comparator node forming a CONNECTED network. Lumping heterogeneous comparators
(e.g. "physician's choice", named regimens, real-world standard of care) into one
node would fabricate a shared comparator — so we DON'T. Distinct comparator-arm
labels are not reliably extractable at abstract level; on such data the network is
reported infeasible (honest) rather than forced. When a connected network exists
(e.g. from full-text/IPD comparator arms), gemtc runs a random-effects NMA and
returns a league table.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_R_NMA = Path(__file__).resolve().parent / "nma.R"


@dataclass
class NMAEdge:
    study: str
    treatment: str
    comparator: str
    diff: float        # log hazard ratio (treatment vs comparator)
    se: float


@dataclass
class NMAResult:
    feasible: bool
    reason: str = ""
    nodes: list = field(default_factory=list)
    league: list = field(default_factory=list)   # [{comparison, estimate, ci_lb, ci_ub}]
    network_png: str = ""


def nma_available() -> bool:
    from core.sr.stats.meta import _has_rscript, rscript_bin
    if not _has_rscript():
        return False
    try:
        out = subprocess.run([rscript_bin(), "-e", "cat(requireNamespace('gemtc', quietly=TRUE))"],
                             capture_output=True, text=True, timeout=30)
        return "TRUE" in out.stdout
    except Exception:
        return False


def _connected(edges: list[NMAEdge], a: str, b: str) -> tuple[bool, set]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        parent[x] = root
        return root

    nodes: set[str] = set()
    for e in edges:
        nodes |= {e.treatment, e.comparator}
        parent[find(e.treatment)] = find(e.comparator)
    return (a in nodes and b in nodes and find(a) == find(b)), nodes


def assess_network(edges: list[NMAEdge], target: tuple[str, str] | None = None) -> tuple[bool, str, set]:
    if target is None:
        nodes = {n for e in edges for n in (e.treatment, e.comparator)}
        return False, "No configured two-arm synthesis target for network meta-analysis.", nodes
    if not edges:
        return False, ("No comparison edges — distinct comparator-arm labels are not available at "
                       "abstract level, so a valid network cannot be formed (needs full-text/IPD)."), set()
    conn, nodes = _connected(edges, *target)
    if target[0] not in nodes or target[1] not in nodes:
        return False, f"Network missing a target node ({' / '.join(target)} not both present).", nodes
    if not conn:
        return False, f"Network disconnected: {target[0]} and {target[1]} share no comparator path.", nodes
    return True, "connected", nodes


def run_nma(edges: list[NMAEdge], fig_dir: Path, target: tuple[str, str] | None = None) -> NMAResult:
    ok, reason, nodes = assess_network(edges, target)
    if not ok:
        return NMAResult(False, reason, sorted(nodes))
    if not nma_available():
        return NMAResult(False, "Network is connected but JAGS/gemtc is not installed.", sorted(nodes))
    import re

    # gemtc treatment ids may only contain letters/digits/underscore — sanitize,
    # then map back to the human node names in the league output.
    names = {e.treatment for e in edges} | {e.comparator for e in edges}
    safe = {n: re.sub(r"[^A-Za-z0-9_]", "_", n) for n in names}
    rev = {v: k for k, v in safe.items()}

    png = fig_dir / "nma_network.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        incsv, outcsv = Path(tmp) / "edges.csv", Path(tmp) / "league.csv"
        # gemtc data.re format: per study, the reference arm has diff=NA.
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["study", "treatment", "diff", "std.err"])
            for e in edges:
                w.writerow([e.study, safe[e.comparator], "NA", "NA"])
                w.writerow([e.study, safe[e.treatment], e.diff, e.se])
        try:
            from core.sr.stats.meta import rscript_bin
            subprocess.run([rscript_bin(), str(_R_NMA), str(incsv), str(outcsv), str(png)],
                           check=True, capture_output=True, timeout=600)
            league = []
            with outcsv.open() as f:
                for d in csv.DictReader(f):
                    comp = " ".join(rev.get(tok, tok) for tok in d["comparison"].split())
                    league.append({"comparison": comp, "estimate": float(d["estimate"]),
                                   "ci_lb": float(d["ci_lb"]), "ci_ub": float(d["ci_ub"])})
            return NMAResult(True, "ok", sorted(nodes), league, png.name if png.exists() else "")
        except Exception as exc:
            return NMAResult(False, f"gemtc run failed: {exc}", sorted(nodes))


_SOC_SYNONYMS = {
    "soc", "standard of care", "standard-of-care", "physician's choice",
    "physicians choice", "physician choice", "real-world soc", "standard therapy",
    "conventional treatment", "conventional care",
}


def _norm_comparator(s: str) -> str:
    """Conservative comparator-node canonicalization (synonyms only; regimens kept distinct).

    Maps common 'standard of care' phrasings to one SoC node and placebo to one
    placebo node. Distinct named regimens (PVd, DPd, Pd, ...) stay separate — we do
    NOT lump different active comparators. Node equivalence still needs human review.
    """
    t = " ".join(s.strip().lower().split())
    if t in _SOC_SYNONYMS:
        return "SoC"
    if "placebo" in t:
        return "placebo"
    return s.strip()[:40]


def build_nma_edges(extractions, modality_by_pmid: dict[str, str],
                    *, subgroup_map: dict[str, list[str]] | None = None,
                    effect_field: str = "pfs_hr_vs_comparator") -> list[NMAEdge]:
    """Build NMA edges from studies that report BOTH a comparator arm and an HR.

    Requires a `comparator_label` extraction field plus a parseable hazard ratio
    (`effect_field`). Studies lacking a comparator label contribute no edge — the
    network is reported infeasible rather than inventing a shared node. Treatment
    nodes are canonicalized via the vertical's subgroup_map (identity when none
    supplied), so unrelated verticals are never forced into another review's buckets.
    """
    import math

    from core.sr.stats.prepare import canonical_subgroup, parse_hr_ci

    edges: list[NMAEdge] = []
    for es in extractions:
        vmap = {v.field: v for v in es.values}
        comp = vmap.get("comparator_label")
        hrv = vmap.get(effect_field)
        if not (comp and comp.flag != "NR" and (comp.value or "").strip().upper() not in ("NR", "")):
            continue
        if not (hrv and hrv.flag != "NR"):
            continue
        parsed = parse_hr_ci(hrv.value)
        if parsed is None:
            continue
        hr, lo, hi = parsed
        se = (math.log(hi) - math.log(lo)) / (2 * 1.96)
        if se <= 0:
            continue
        raw = es.modality or modality_by_pmid.get(es.pmid, "")
        treatment = canonical_subgroup(raw, subgroup_map)
        edges.append(NMAEdge(study=es.pmid, treatment=treatment,
                             comparator=_norm_comparator(comp.value), diff=math.log(hr), se=se))
    return edges
