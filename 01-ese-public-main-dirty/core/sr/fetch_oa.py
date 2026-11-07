"""Harvest LEGAL open-access full text for a run's included studies.

Sources, in priority order, are all author/publisher-authorized open access:
  user-supplied PDF (institutional, licensed) -> PMC OA -> Europe PMC OA -> Unpaywall.
Each hit is SAVED to the destination folder (raw artifact + extracted `.fulltext.txt`)
so downstream extraction agents can read it. Studies with no legal OA copy are reported
honestly as "needs institutional access" — never fabricated, and NO paywall/Sci-Hub
circumvention is ever attempted.

    python -m core.sr.fetch_oa runs/<id> [--dest ~/Desktop/benchmark-fulltext]
    python -m core.sr.fetch_oa runs/<id> --overlay-only   # merge fulltext_<pmid>.json -> pool
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from core.connectors.europepmc import EuropePmcConnector
from core.connectors.pmc import PmcConnector, _extract_body_text
from core.connectors.unpaywall import UnpaywallConnector, _pdf_bytes_to_text, _strip_html
from core.sr.fulltext import _MIN_CHARS, coverage_summary, pdf_to_text

DEFAULT_DEST = Path("~/Desktop/benchmark-fulltext").expanduser()


@dataclass
class OAResult:
    pmid: str
    doi: str
    source: str            # user_file | pmc | europepmc | unpaywall | none
    saved_path: str = ""
    chars: int = 0
    note: str = ""


def _doi_safe(doi: str) -> str:
    return (doi or "").replace("/", "_").replace(":", "_").strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_user_pdf(dest: Path, pmid: str, doi: str) -> Optional[Path]:
    """Find a user-supplied PDF for this study, robust to messy real-world filenames.

    Matches any *.pdf in dest whose normalized name contains the normalized DOI or the
    PMID — so '10.1007:s00535-...pdf', 'x.pdf .pdf' double-extensions, or '<pmid>.pdf'
    all resolve. Skips the harvester's own saved artifacts (*.oa.pdf).
    """
    nd, np = _norm(doi), _norm(pmid)
    for f in sorted(dest.glob("*.pdf")):
        if f.name.endswith(".oa.pdf"):
            continue
        nf = _norm(f.name)
        if (nd and nd in nf) or (np and len(np) >= 5 and np in nf):
            return f
    return None


def _load_manifest(dest: Path) -> dict[str, dict]:
    """{pmid: {doi, stem}} from dest/MANIFEST.csv (suggested_filename without extension)."""
    out: dict[str, dict] = {}
    mf = dest / "MANIFEST.csv"
    if not mf.exists():
        return out
    for row in csv.DictReader(mf.open()):
        pmid = (row.get("pmid") or "").strip()
        if not pmid:
            continue
        doi = (row.get("doi") or "").strip()
        if doi.upper() in ("", "NO_DOI"):
            doi = ""
        stem = Path((row.get("suggested_filename") or f"{pmid}.pdf").strip()).stem
        out[pmid] = {"doi": doi, "stem": stem}
    return out


def _stem_for(pmid: str, doi: str, manifest: dict[str, dict]) -> str:
    if pmid in manifest and manifest[pmid].get("stem"):
        return manifest[pmid]["stem"]
    return _doi_safe(doi) or pmid


async def _doi_map(run_dir: Path, dest: Path) -> dict[str, str]:
    m: dict[str, str] = {}
    for pmid, info in _load_manifest(dest).items():
        if info.get("doi"):
            m[pmid] = info["doi"]
    for f in run_dir.glob("fulltext_*.json"):
        try:
            d = json.loads(f.read_text())
            if d.get("doi"):
                m[d.get("pmid", f.stem.replace("fulltext_", ""))] = d["doi"]
        except Exception:
            continue
    return m


def _write(dest: Path, name: str, data) -> Path:
    p = dest / name
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8")
    return p


async def _resolve_one(pmid, doi, stem, dest, pmc, epmc, uw) -> OAResult:
    # 1) user-supplied licensed PDF already in the folder (robust filename match)
    f = find_user_pdf(dest, pmid, doi)
    if f is not None:
        t = pdf_to_text(f)
        if len(t) >= _MIN_CHARS:
            _write(dest, f"{stem}.fulltext.txt", t)
            return OAResult(pmid, doi, "user_file", str(f), len(t))

    # 2) PMC open access (raw JATS XML preserves tables)
    xml = await pmc.fulltext_xml(pmid)
    if xml:
        t = _extract_body_text(xml)
        if len(t) >= _MIN_CHARS:
            _write(dest, f"{stem}.pmc.xml", xml)
            _write(dest, f"{stem}.fulltext.txt", t)
            return OAResult(pmid, doi, "pmc", str(dest / f"{stem}.pmc.xml"), len(t))

    # 3) Europe PMC open access
    loc = await epmc.oa_locator(pmid)
    if loc:
        xml = await epmc.oa_fulltext_xml(*loc)
        if xml:
            t = _extract_body_text(xml) or _strip_html(xml)
            if len(t) >= _MIN_CHARS:
                _write(dest, f"{stem}.epmc.xml", xml)
                _write(dest, f"{stem}.fulltext.txt", t)
                return OAResult(pmid, doi, "europepmc", str(dest / f"{stem}.epmc.xml"), len(t))

    # 4) Unpaywall best_oa_location (author-accepted manuscript etc.)
    if doi:
        art = await uw.oa_artifact(doi)
        if art:
            content, ctype, url = art
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                t = _pdf_bytes_to_text(content) or ""
                if len(t) >= _MIN_CHARS:
                    _write(dest, f"{stem}.oa.pdf", content)
                    _write(dest, f"{stem}.fulltext.txt", t)
                    return OAResult(pmid, doi, "unpaywall", str(dest / f"{stem}.oa.pdf"), len(t))
            else:
                t = _strip_html(content.decode("utf-8", "ignore"))
                if len(t) >= _MIN_CHARS:
                    _write(dest, f"{stem}.oa.html", content)
                    _write(dest, f"{stem}.fulltext.txt", t)
                    return OAResult(pmid, doi, "unpaywall", str(dest / f"{stem}.oa.html"), len(t))

    return OAResult(pmid, doi, "none", "", 0, "no legal OA copy — needs institutional access")


async def harvest_oa(pmids: list[str], doi_by_pmid: dict[str, str], dest: Path,
                     *, concurrency: int = 4) -> list[OAResult]:
    dest.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(dest)
    pmc, epmc, uw = PmcConnector(), EuropePmcConnector(), UnpaywallConnector()
    sem = asyncio.Semaphore(concurrency)

    async def _one(pmid: str) -> OAResult:
        async with sem:
            doi = doi_by_pmid.get(pmid, "")
            if not doi:                       # lazy DOI lookup (needed only for Unpaywall)
                doi = await pmc.doi_for(pmid) or ""
            stem = _stem_for(pmid, doi, manifest)
            return await _resolve_one(pmid, doi, stem, dest, pmc, epmc, uw)

    try:
        return list(await asyncio.gather(*(_one(p) for p in pmids)))
    finally:
        await pmc.aclose(); await epmc.aclose(); await uw.aclose()


def write_report(results: list[OAResult], dest: Path) -> Path:
    from core.sr.fulltext import FullText
    cov = coverage_summary([FullText(r.pmid, r.source != "none", r.source, "", r.chars)
                            for r in results])
    report = {"summary": cov,
              "studies": [asdict(r) for r in sorted(results, key=lambda r: (r.source == "none", r.pmid))],
              "pmid_to_file": {r.pmid: r.saved_path for r in results if r.saved_path}}
    p = dest / "fetch_report.json"
    p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return p


def overlay_fulltext(run_dir: Path) -> dict:
    """Merge fulltext_<pmid>.json scalar values into extractions_merged.json IN PLACE.

    Only fills fields the abstract left NR (never overwrites a grounded abstract value).
    Dose-table dicts are left in the fulltext files for the dose-response step.
    """
    merged_path = run_dir / "extractions_merged.json"
    studies = json.loads(merged_path.read_text())
    changed: list[str] = []
    for study in studies:
        pmid = study.get("pmid", "")
        ftp = run_dir / f"fulltext_{pmid}.json"
        if not ftp.exists():
            continue
        ft = json.loads(ftp.read_text())
        by_field = {v["field"]: v for v in study.get("values", [])}
        for field, val in ft.items():
            if field not in by_field or not isinstance(val, str):
                continue
            v = by_field[field]
            sval = val.strip()
            if v.get("flag") == "NR" and sval and not sval.upper().startswith("NR"):
                v["value"], v["flag"] = sval, ""
                v["quote"] = v.get("quote") or f"[full-text {pmid}: {sval}]"
                changed.append(f"{pmid}:{field}")
    merged_path.write_text(json.dumps(studies, indent=2), encoding="utf-8")
    return {"updated_fields": changed, "n": len(changed)}


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="fetch_oa", description="Harvest LEGAL open-access full text.")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--overlay-only", action="store_true",
                    help="Skip harvesting; merge existing fulltext_<pmid>.json into the pool.")
    args = ap.parse_args(argv)
    run_dir = args.run_dir.expanduser().resolve()

    if args.overlay_only:
        res = overlay_fulltext(run_dir)
        print(f"overlay: {res['n']} field(s) filled from full text: {res['updated_fields']}")
        return

    dest = args.dest.expanduser()
    includes = json.loads((run_dir / "includes.json").read_text())
    pmids = [d["pmid"] if isinstance(d, dict) else d for d in includes]
    doi_map = asyncio.run(_doi_map(run_dir, dest))
    results = asyncio.run(harvest_oa(pmids, doi_map, dest, concurrency=args.concurrency))
    write_report(results, dest)
    cov = coverage_summary_str(results)
    print(cov)
    print(f"report -> {dest / 'fetch_report.json'}")


def coverage_summary_str(results: list[OAResult]) -> str:
    by: dict[str, int] = {}
    for r in results:
        by[r.source] = by.get(r.source, 0) + 1
    have = sum(1 for r in results if r.source != "none")
    lines = [f"Legal OA harvest: {have}/{len(results)} with full text  {dict(sorted(by.items()))}"]
    for r in sorted(results, key=lambda r: (r.source == "none", r.pmid)):
        tag = r.source if r.source != "none" else "MISS (institutional access needed)"
        lines.append(f"  {r.pmid:9} {tag:12} {Path(r.saved_path).name if r.saved_path else ''}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
