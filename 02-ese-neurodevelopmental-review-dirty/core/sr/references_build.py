"""Verified reference-library populator + citation-format bridge (WP7 / Piece 2).

Builds ``run_dir/references.json`` — the keyed, verified bibliography that ARMS the
fail-closed reference-integrity gate in ``core/sr/references.py``. V1 scope: included
studies only, keyed by a stable ``PMID<n>`` key, each verified live (PMID via the
PubMed esummary round-trip, DOI via Crossref when a DOI is known). Grounding the
bibliography on a fixed, live-verified pool prevents citation fabrication by
construction.

It also bridges the citation-format split (gap G4): narrative prose cites included
studies as ``[PMID: n]``, while the gate and the numbered bibliography key on a bare
bracket ``[PMID<n>]``. :func:`normalize_pmid_citations` rewrites the former to the
latter so the gate can actually protect the prose and the in-text tokens line up with
the reference keys.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Prose emits ``[PMID: n]`` (and comma lists ``[PMID: n, m]``); the gate
# (references.py::_KEY_RE) + the keyed bibliography use a bare bracket ``[PMID<n>]``.
_PMID_CITE_RE = re.compile(r"\[PMID:\s*([\d,\s]+?)\]", re.I)

# Reference-entry fields consumed by references.py::_vancouver, in a stable order.
_ENTRY_FIELDS = ("authors", "title", "journal", "year", "volume", "pages")


def stable_key(pmid) -> str:
    """The V1 stable citation key for an included study: ``PMID<n>``."""
    return f"PMID{str(pmid).strip()}"


def _expand_pmid_citation(m: "re.Match") -> str:
    """``[PMID: n]`` -> ``[PMIDn]``; ``[PMID: n, m]`` -> ``[PMIDn] [PMIDm]`` so every cited
    PMID becomes its own gated key. A token with no digits is left untouched."""
    pmids = [t.strip() for t in m.group(1).split(",") if t.strip().isdigit()]
    return " ".join(f"[{stable_key(p)}]" for p in pmids) if pmids else m.group(0)


def normalize_pmid_citations(text: str) -> str:
    """Rewrite prose ``[PMID: n]`` (and comma lists) to the bare-bracket ``[PMID<n>]`` keys
    the reference gate and the keyed bibliography use. Idempotent — an already-bare
    ``[PMID<n>]`` is left untouched (it does not match ``[PMID: n]``)."""
    return _PMID_CITE_RE.sub(_expand_pmid_citation, text or "")


def _live_verify(pmids: list[str], dois: list[str]):
    """(verified_pmids, verified_dois) via the same PubMed/Crossref path as the rest of
    the engine. Network; callers/tests inject a deterministic checker instead."""
    from core.connectors.crossref import verify_dois
    from core.connectors.pubmed import PubMedConnector
    from core.sr.orchestrator.adapters import run_async

    async def _run():
        conn = PubMedConnector()
        try:
            vp = await conn.verify_bulk(pmids) if pmids else set()
        finally:
            await conn.aclose()
        vd = await verify_dois(dois) if dois else set()
        return vp, vd

    return run_async(_run())


def build_reference_library(records: dict[str, dict], included_pmids, *, verifier=None) -> dict:
    """Build ``{key: entry}`` for the included studies, each ``verified`` per a live
    PMID/DOI round-trip. ``verifier(pmids, dois) -> (set, set)`` overrides the live path
    (used by tests / offline callers). Metadata is taken from the grounded record set;
    absent fields default to "" so ``references.py::_vancouver`` degrades gracefully.
    """
    pmids = [str(p) for p in included_pmids]
    doi_by_pmid = {p: (records.get(p, {}).get("doi") or "").strip() for p in pmids}
    doi_list = [d for d in doi_by_pmid.values() if d]
    verify = verifier if verifier is not None else _live_verify
    verified_pmids, verified_dois = verify(pmids, doi_list)

    lib: dict[str, dict] = {}
    for p in pmids:
        r = records.get(p, {})
        doi = doi_by_pmid[p]
        is_verified = (p in verified_pmids) or (bool(doi) and doi in verified_dois)
        entry = {f: (r.get(f) or "") for f in _ENTRY_FIELDS}
        # ``year`` may be an int in records_for_agents.json — keep it as-is when present.
        if r.get("year") not in (None, ""):
            entry["year"] = r["year"]
        entry.update({"pmid": p, "doi": doi, "verified": bool(is_verified)})
        lib[stable_key(p)] = entry
    return lib


def _included_pmids(run_dir: Path) -> list[str]:
    """Included-study PMIDs: prefer the builtin-tool ``includes.json``; fall back to the
    INCLUDE decisions in the orchestrator's ``screening.json``/``screening_merged.json``."""
    inc = run_dir / "includes.json"
    if inc.exists():
        return [str(p) for p in json.loads(inc.read_text(encoding="utf-8"))]
    from core.sr.agent_io import load_screening
    from core.sr.models import ScreenDecision
    for name in ("screening.json", "screening_merged.json"):
        p = run_dir / name
        if p.exists():
            screening = load_screening(json.loads(p.read_text(encoding="utf-8")))
            return [s.pmid for s in screening if s.decision == ScreenDecision.INCLUDE]
    return []


def write_references(run_dir, *, verifier=None) -> Path:
    """Build + write ``run_dir/references.json`` from the included studies. Arming the
    opt-in reference gate is a deliberate act (running this), so a run without it stays
    ungated/dormant."""
    run_dir = Path(run_dir)
    records = {str(r["pmid"]): r
               for r in json.loads((run_dir / "records_for_agents.json").read_text(encoding="utf-8"))}
    lib = build_reference_library(records, _included_pmids(run_dir), verifier=verifier)
    out = run_dir / "references.json"
    out.write_text(json.dumps(lib, indent=2, sort_keys=True), encoding="utf-8")
    return out
