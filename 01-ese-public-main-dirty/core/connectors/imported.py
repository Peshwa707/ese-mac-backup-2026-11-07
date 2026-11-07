"""Generic import connector — ingest exported records from any database.

The realistic path for paywalled databases (Cochrane CENTRAL, Embase, Web of
Science) you have institutional access to: export search results as RIS / NBIB /
CSV / PubMed-XML and drop them in the imports directory. The inbox is scoped
PER-REVIEW so one review's exports never leak into another:
``~/Documents/EvidenceEngine/_search_imports/<vertical>/<database>/`` (override the
base with ``EVIDENCE_IMPORTS_DIR``). Each record becomes a provenance-ATTESTED
Citation (the export file + database are logged) — distinct from live API-verified
records, but still grounded and traceable.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from core.connectors.base import Connector
from core.models import Citation, IdType, RetrievalResult
from core.paths import ENGINE_BASE, path_env

IMPORTS_DIR = path_env("EVIDENCE_IMPORTS_DIR", default=ENGINE_BASE / "_search_imports")
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")

# Sources that are SUPPLEMENTARY, not a reproducible systematic database search —
# reported separately in the PRISMA flow (Cochrane Ch 4). Compared after normalizing
# the folder name to letters only, so "Google Scholar"/"google_scholar"/"google-scholar"
# all match.
_SUPPLEMENTARY = {
    "googlescholar", "gscholar", "scholar", "handsearch", "handsearching",
    "citationchasing", "citationchase", "citations", "referencelists",
    "greyliterature", "greylit",
}


def is_supplementary(database: str) -> bool:
    return re.sub(r"[^a-z]", "", (database or "").lower()) in _SUPPLEMENTARY


_README = """# Search-import inbox

Drop exported search results here to include databases that have no free API
(Cochrane CENTRAL, Embase) or need institutional access (Web of Science, Scopus),
plus supplementary sources (Google Scholar, hand-searching).

## How
- The inbox is scoped PER REVIEW (vertical): `_search_imports/<vertical>/<database>/`.
  A review only picks up imports under its own vertical folder, so exports never
  leak between reviews. E.g. for the antithrombin review:
  `_search_imports/antithrombin-preeclampsia/Embase/`,
  `_search_imports/antithrombin-preeclampsia/CENTRAL/`,
  `_search_imports/antithrombin-preeclampsia/GoogleScholar/`
  (create the folder, then drop the export file in it).
- Supported export formats: `.ris`, `.nbib`, `.csv`, `.xml` (PubMed).
- Records are marked "provenance-attested" (the export file is logged) and deduped
  against the live sources by DOI / PMID / title.

## Exporting
Replace `<vertical>` below with your review's vertical name (e.g. `antithrombin-preeclampsia`);
files must go under that per-review folder or they will NOT be read (they'll be flagged
as a legacy flat-layout warning instead).
- **Embase** (embase.com, institutional login): run your search → Export → **RIS**
  (Mendeley/EndNote format) → save into `_search_imports/<vertical>/Embase/`. Up to 10,000 per file.
- **Cochrane CENTRAL** (Cochrane Library): Export selected → **RIS** → `_search_imports/<vertical>/CENTRAL/`.
- **Web of Science / Scopus**: prefer the live API (set the key in providers.env); or
  Export → **RIS/CSV** into `_search_imports/<vertical>/WebofScience/` or `_search_imports/<vertical>/Scopus/`.
- **Google Scholar** (SUPPLEMENTARY — no Boolean/reproducible export): export the top
  results via your reference manager (or Publish or Perish) as RIS/CSV into
  `_search_imports/<vertical>/GoogleScholar/`. These are reported as a *supplementary* source,
  not a systematic database search (Cochrane Handbook Ch 4).
"""


# --- format parsers (return list of flat record dicts) ---------------------

def parse_ris(text: str) -> list[dict]:
    records, cur = [], {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - (.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            if cur:
                records.append(cur)
            cur = {}
        elif tag in ("TI", "T1") and "title" not in cur:
            cur["title"] = val
        elif tag in ("AB", "N2"):  # N2 = abstract in Embase/Ovid RIS exports
            cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
        elif tag in ("PY", "Y1"):
            cur["year"] = val
        elif tag == "DO":
            cur["doi"] = val
        elif tag in ("AN", "ID", "C5") and _looks_pmid(val):  # C5 = PMID in Embase RIS
            cur.setdefault("pmid", _digits(val))
        elif tag == "M3":  # Embase/Ovid record type (e.g. "Conference Abstract", "Article")
            cur.setdefault("study_type", val)
        elif tag == "ER":
            records.append(cur)
            cur = {}
    if cur:
        records.append(cur)
    return [r for r in records if r.get("title")]


def parse_nbib(text: str) -> list[dict]:
    records, cur, last = [], {}, None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = re.match(r"^([A-Z]{2,4})\s*- (.*)$", raw)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            last = tag
            if tag == "PMID":
                if cur:
                    records.append(cur)
                cur = {"pmid": val}
            elif tag == "TI":
                cur["title"] = val
            elif tag == "AB":
                cur["abstract"] = val
            elif tag == "DP":
                cur["year"] = val
            elif tag == "AID" and "[doi]" in val:
                cur["doi"] = val.replace("[doi]", "").strip()
        elif raw.startswith(" ") and last:   # continuation line
            key = {"TI": "title", "AB": "abstract"}.get(last)
            if key:
                cur[key] = (cur.get(key, "") + " " + raw.strip()).strip()
    if cur:
        records.append(cur)
    return [r for r in records if r.get("title")]


def parse_csv(text: str) -> list[dict]:
    out = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        rec = {
            "title": low.get("title") or low.get("article title") or "",
            "abstract": low.get("abstract") or "",
            "doi": low.get("doi") or "",
            "pmid": low.get("pmid") or low.get("pubmed id") or "",
            "year": low.get("year") or low.get("publication year") or "",
        }
        if rec["title"]:
            out.append(rec)
    return out


def _looks_pmid(v: str) -> bool:
    return "medline" in v.lower() or v.strip().isdigit()


def _digits(v: str) -> str:
    d = re.sub(r"\D", "", v)
    return d


def to_citation(rec: dict, database: str, filename: str) -> Citation | None:
    title = (rec.get("title") or "").strip()
    if not title:
        return None
    pmid = (rec.get("pmid") or "").strip()
    doi = (rec.get("doi") or "").strip()
    if not doi:
        m = _DOI_RE.search(rec.get("abstract", "") or "")
        doi = m.group(0) if m else ""
    ym = _YEAR_RE.search(str(rec.get("year", "")))
    year = int(ym.group(0)) if ym else None
    if pmid:
        id_type, cid = IdType.PMID, pmid
    elif doi:
        id_type, cid = IdType.DOI, doi
    else:
        # Deterministic id (NOT Python's per-process-salted hash()): a PMID/DOI-less
        # import must get the SAME id every run so it round-trips across corpus rebuilds
        # / resume / full-text overlay.
        import hashlib
        _key = re.sub(r"\s+", " ", title.lower()).strip()
        id_type, cid = IdType.URL, "import:" + hashlib.sha1(_key.encode("utf-8")).hexdigest()[:12]
    return Citation(
        id_type=id_type, id=cid, source=database, title=title,
        snippet=rec.get("abstract", "") or "", year=year, doi=doi,
        study_type=rec.get("study_type", "") or None,
        extra_ids={"PMID": pmid} if (pmid and id_type != IdType.PMID) else {},
        provenance=filename, verification="unverified",
        supplementary=is_supplementary(database),
    )


def parse_file(path: Path, database: str) -> list[Citation]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    ext = path.suffix.lower()
    if ext == ".ris":
        recs = parse_ris(text)
    elif ext == ".nbib":
        recs = parse_nbib(text)
    elif ext == ".csv":
        recs = parse_csv(text)
    elif ext == ".xml":
        from core.connectors.pubmed import _parse_efetch
        cites = _parse_efetch(text)
        supp = is_supplementary(database)
        for c in cites:
            c.source, c.provenance, c.verification = database, path.name, "unverified"
            c.supplementary = supp
        return cites
    else:
        return []
    return [c for r in recs if (c := to_citation(r, database, path.name)) is not None]


class ImportedConnector(Connector):
    """Returns the parsed records for one database's export folder (query ignored)."""

    def __init__(self, database: str, folder: Path):
        super().__init__()
        self.source = database
        self.folder = folder

    async def search(self, query: str, structured: dict[str, Any]) -> RetrievalResult:
        cites: list[Citation] = []
        for f in sorted(self.folder.glob("*")):
            if f.suffix.lower() in (".ris", ".nbib", ".csv", ".xml"):
                cites.extend(parse_file(f, self.source))
        return RetrievalResult(source=self.source, query=f"[import] {self.folder.name}", citations=cites)

    async def verify(self, citation: Citation) -> bool:
        return False   # attested by provenance, not API-verified


_EXPORT_EXTS = (".ris", ".nbib", ".csv", ".xml")


def vertical_inbox(vertical: str, base: Path = IMPORTS_DIR) -> Path:
    """The per-review import folder: ``<base>/<vertical>``. Reviews only read their
    own vertical's imports so exports never leak between reviews."""
    return base / (vertical or "_unscoped")


def legacy_flat_exports(base: Path = IMPORTS_DIR) -> list[str]:
    """Top-level folders holding export files DIRECTLY (the pre–per-vertical flat
    layout ``<base>/<database>/``). These are no longer read (imports are now scoped
    to ``<base>/<vertical>/<database>/``), so they must be surfaced loudly rather than
    silently dropped. A folder that only contains sub-folders is a vertical inbox, not
    a legacy database folder, and is not reported here."""
    if not base.exists():
        return []
    out = []
    for sub in sorted(p for p in base.iterdir() if p.is_dir()):
        if any(f.suffix.lower() in _EXPORT_EXTS for f in sub.glob("*") if f.is_file()):
            out.append(sub.name)
    return out


def ensure_imports_dir(base: Path = IMPORTS_DIR) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    readme = base / "README.md"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
    return base


def imported_connectors(base: Path = IMPORTS_DIR) -> list[ImportedConnector]:
    """One connector per database subfolder that contains export files.

    Read-only: returns [] when the inbox does not exist (so running imports by
    default has zero side effects until the user creates a folder and drops a
    file). Call ``ensure_imports_dir`` explicitly to scaffold the inbox + README.
    """
    if not base.exists():
        return []
    out = []
    for sub in sorted(base.iterdir()):
        if sub.is_dir() and any(f.suffix.lower() in (".ris", ".nbib", ".csv", ".xml")
                                for f in sub.glob("*")):
            out.append(ImportedConnector(sub.name, sub))
    return out
