"""Verified reference library + citation-integrity gate (WP7).

The manuscript's bibliography must structurally never contain a fabricated or broken
reference. This module loads a verified reference library, checks that every in-text
``[key]`` resolves to a verified entry (and flags orphans), renders a Vancouver list
numbered by first appearance, and exposes a fail-closed gate for manuscript export.

In-text citation form is a stable key in brackets, e.g. ``[Smith2023]``; keys are
converted to sequential numbers at export. Pure Python (verification uses the same
PubMed/Crossref path as the rest of the engine, injected so this module stays testable).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Bracket tokens that are NOT citations (status flags, abbreviations defined in text,
# and pooled-result subgroup labels such as "[ALL]"/"[ANY]" that appear in league tables
# and forest-plot prose). A fabricated citation key (e.g. "[GHOST]", "[Smith2023]") still
# falls through to `broken`, so this exemption does not weaken the fail-closed export gate.
_NON_CITATION = {"pending", "x", "tbd", "to be completed", "hr", "ci", "or", "rr", "nr",
                 "all", "any"}
_KEY_RE = re.compile(r"\[([A-Za-z][\w\-]*\d?)\]")


class ReferenceIntegrityError(RuntimeError):
    """Raised when a manuscript cites a key with no verified library entry."""


def load_library(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"reference library is not an object: {p}")
    return data


def _cited_keys(text: str, lib: dict) -> list[str]:
    """Keys from the library that appear in text, in order of first appearance."""
    first: dict[str, int] = {}
    for key in lib:
        m = re.search(r"\[" + re.escape(key) + r"\]", text)
        if m:
            first[key] = m.start()
    return sorted(first, key=lambda k: first[k])


def check_integrity(text: str, lib: dict) -> dict:
    """Return {cited, broken, orphan}.

    - cited:  library keys referenced in text (order of first appearance)
    - broken: bracket tokens that look like citations but have NO library entry
    - orphan: library entries never cited in text
    """
    cited = _cited_keys(text, lib)
    known = set(lib)
    tokens = set(_KEY_RE.findall(text))
    broken = sorted(t for t in tokens
                    if t not in known and t.lower() not in _NON_CITATION
                    and not t.upper().startswith("PENDING"))
    orphan = sorted(k for k in lib if k not in cited)
    return {"cited": cited, "broken": broken, "orphan": orphan}


def _vancouver(entry: dict, n: int) -> str:
    bits = [f"{n}. {(entry.get('authors') or '').strip().rstrip('.')}." if entry.get("authors") else f"{n}."]
    if entry.get("title"):
        bits.append(f"{entry['title'].rstrip('.')}.")
    tail = entry.get("journal", "")
    if entry.get("year"):
        tail += f". {entry['year']}"
    if entry.get("volume"):
        tail += f";{entry['volume']}"
    if entry.get("pages"):
        tail += f":{entry['pages']}"
    if tail.strip():
        bits.append(tail.strip().lstrip(".").strip() + ".")
    ids = []
    if entry.get("pmid"):
        ids.append(f"PMID: {entry['pmid']}")
    if entry.get("doi"):
        ids.append(f"doi:{entry['doi']}")
    if ids:
        bits.append("(" + "; ".join(ids) + ")")
    return " ".join(bits)


def render_vancouver(text: str, lib: dict) -> list[str]:
    """Numbered reference list (Vancouver), ordered by first appearance in text."""
    return [_vancouver(lib[k], i + 1) for i, k in enumerate(_cited_keys(text, lib))]


def key_to_number(text: str, lib: dict) -> dict[str, int]:
    """Map each cited key -> its Vancouver number (for [key] -> [n] substitution)."""
    return {k: i + 1 for i, k in enumerate(_cited_keys(text, lib))}


def gate_run_dir(run_dir, text: str, require_verified: bool = True):
    """Opt-in manuscript export gate. If ``run_dir/references.json`` EXISTS, enforce
    citation integrity on ``text`` (fail closed) — even when the library is empty, since
    any ``[key]`` is then broken. No-op (returns None) only when the file is ABSENT, so
    runs without a key-based library are unaffected. Returns the report on success."""
    p = Path(run_dir) / "references.json"
    if not p.exists():
        return None
    return assert_export_ok(text, load_library(p), require_verified=require_verified)


def assert_export_ok(text: str, lib: dict, require_verified: bool = True) -> dict:
    """Fail-closed gate for manuscript export. Raises ReferenceIntegrityError on any
    broken citation, or (when require_verified) any cited entry not marked verified.
    Returns the integrity report on success."""
    report = check_integrity(text, lib)
    if report["broken"]:
        raise ReferenceIntegrityError(f"broken citations (no library entry): {report['broken']}")
    if require_verified:
        unverified = [k for k in report["cited"] if not lib[k].get("verified")]
        if unverified:
            raise ReferenceIntegrityError(f"cited but unverified references: {unverified}")
    return report
