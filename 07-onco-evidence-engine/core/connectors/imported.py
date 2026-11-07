"""Generic import connector — ingest exported records from any database.

The realistic path for paywalled databases (Cochrane CENTRAL, Embase, Web of
Science) you have institutional access to: export search results as RIS / NBIB /
CSV / PubMed-XML and drop them in the imports directory (default
``~/Documents/EvidenceEngine/_search_imports/<database>/``; override with
``EVIDENCE_IMPORTS_DIR``). Each record becomes a provenance-ATTESTED Citation (the
export file + database are logged) — distinct from live API-verified records, but
still grounded and traceable.
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

_README = """# Search-import inbox

Drop exported search results here to include paywalled / non-API databases
(Cochrane CENTRAL, Embase, Web of Science) and conference abstracts via your
institutional access.

- One subfolder per database: `_search_imports/CENTRAL/`, `_search_imports/Embase/`, etc.
- Supported export formats: `.ris`, `.nbib`, `.csv`, `.xml` (PubMed).
- Records are marked "provenance-attested" (the export file is logged) and deduped
  against the live sources by DOI / PMID / title.
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
        id_type, cid = IdType.URL, f"import:{abs(hash(title)) % 10**10}"
    return Citation(
        id_type=id_type, id=cid, source=database, title=title,
        snippet=rec.get("abstract", "") or "", year=year, doi=doi,
        study_type=rec.get("study_type", "") or None,
        extra_ids={"PMID": pmid} if (pmid and id_type != IdType.PMID) else {},
        provenance=filename, verification="unverified",
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
        for c in cites:
            c.source, c.provenance, c.verification = database, path.name, "unverified"
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


def ensure_imports_dir(base: Path = IMPORTS_DIR) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    readme = base / "README.md"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
    return base


def imported_connectors(base: Path = IMPORTS_DIR) -> list[ImportedConnector]:
    """One connector per database subfolder that contains export files."""
    ensure_imports_dir(base)
    out = []
    for sub in sorted(base.iterdir()):
        if sub.is_dir() and any(f.suffix.lower() in (".ris", ".nbib", ".csv", ".xml")
                                for f in sub.glob("*")):
            out.append(ImportedConnector(sub.name, sub))
    return out
