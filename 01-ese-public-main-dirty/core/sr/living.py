"""Living systematic review — delta detection against a prior run.

A living review re-runs the search on a schedule and processes only what is NEW
since the last run: identify records not previously screened, screen/extract just
those (via the normal agent stages), then merge with the carried-forward prior
decisions. This turns a one-shot review into a standing, updatable asset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Delta:
    new_records: list[dict]       # records to screen this cycle (not seen before)
    carried_pmids: list[str]      # previously screened, carried forward unchanged
    n_new: int
    n_carried: int


def previous_screened_pmids(prev_run_dir: Path) -> set[str]:
    """PMIDs already screened in a prior run (carried forward)."""
    p = prev_run_dir / "screening_merged.json"
    if not p.exists():
        return set()
    return {d.get("pmid") for d in json.loads(p.read_text()) if d.get("pmid")}


def compute_delta(new_records: list[dict], seen_pmids: set[str]) -> Delta:
    """Split a freshly-retrieved corpus into new vs already-seen."""
    new = [r for r in new_records if r.get("pmid") not in seen_pmids]
    carried = sorted(seen_pmids)
    return Delta(new_records=new, carried_pmids=carried, n_new=len(new), n_carried=len(carried))


def merge_screening(prev_screening: list[dict], new_screening: list[dict]) -> list[dict]:
    """Combine carried-forward decisions with this cycle's new decisions (new wins)."""
    by_pmid = {d["pmid"]: d for d in prev_screening if d.get("pmid")}
    for d in new_screening:
        if d.get("pmid"):
            by_pmid[d["pmid"]] = d
    return list(by_pmid.values())


def delta_summary(delta: Delta) -> dict:
    return {"new_this_cycle": delta.n_new, "carried_forward": delta.n_carried,
            "total": delta.n_new + delta.n_carried}
