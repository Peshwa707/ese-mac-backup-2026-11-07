"""Verified reference-library populator + citation bridge + fail-closed gate (Piece 2)."""
from __future__ import annotations

import json

import pytest

from core.sr.config import load_sr_config
from core.sr.manuscript.assemble import build_components
from core.sr.manuscript.model import BulletList, Paragraph
from core.sr.references import ReferenceIntegrityError, gate_run_dir, load_library
from core.sr.references_build import (
    build_reference_library,
    normalize_pmid_citations,
    stable_key,
    write_references,
)
from tests.test_sr_manuscript import _RECORDS, _synthetic_run


def test_normalize_pmid_citations_bridges_format():
    assert normalize_pmid_citations("ORR 84% [PMID: 111] and [PMID:222].") == \
        "ORR 84% [PMID111] and [PMID222]."
    # Idempotent: already-bare keys are untouched.
    assert normalize_pmid_citations("[PMID111]") == "[PMID111]"


def test_build_reference_library_keys_and_verifies():
    records = {"111": {"pmid": "111", "title": "A trial of X", "year": 2024, "doi": "10.1/x"}}

    def _verifier(pmids, dois):
        return {"111"}, set()   # PMID resolves live; no DOI check needed

    lib = build_reference_library(records, ["111"], verifier=_verifier)
    assert stable_key("111") == "PMID111"
    entry = lib["PMID111"]
    assert entry["pmid"] == "111" and entry["title"] == "A trial of X"
    assert entry["year"] == 2024 and entry["doi"] == "10.1/x"
    assert entry["verified"] is True


def test_build_reference_library_marks_unverified_when_neither_id_resolves():
    records = {"111": {"pmid": "111", "title": "T", "year": 2024}}
    lib = build_reference_library(records, ["111"], verifier=lambda p, d: (set(), set()))
    assert lib["PMID111"]["verified"] is False


def test_write_references_from_run_dir(tmp_path):
    (tmp_path / "records_for_agents.json").write_text(
        json.dumps([{"pmid": "111", "title": "A trial of X", "year": 2024}]), encoding="utf-8")
    (tmp_path / "includes.json").write_text(json.dumps(["111"]), encoding="utf-8")
    out = write_references(tmp_path, verifier=lambda p, d: (set(p), set()))
    lib = load_library(out)
    assert "PMID111" in lib and lib["PMID111"]["verified"] is True


def _write_lib(run_dir, verified=True):
    lib = {"PMID111": {"authors": "Smith J", "title": "A trial of X", "journal": "J Onc",
                       "year": 2024, "volume": "12", "pages": "1-9", "pmid": "111",
                       "doi": "", "verified": verified}}
    (run_dir / "references.json").write_text(json.dumps(lib), encoding="utf-8")
    return lib


def test_gate_blocks_fabricated_citation(tmp_path):
    """references.json present + prose citing an unknown key -> export blocked."""
    _write_lib(tmp_path)
    prose = normalize_pmid_citations("Real [PMID: 111] but fake [PMID: 999] and [GHOST].")
    with pytest.raises(ReferenceIntegrityError):
        gate_run_dir(tmp_path, prose)


def test_gate_passes_clean_prose(tmp_path):
    """references.json present + prose citing only verified keys -> export allowed."""
    _write_lib(tmp_path)
    prose = normalize_pmid_citations("Grounded finding [PMID: 111].")
    report = gate_run_dir(tmp_path, prose)
    assert report is not None and report["broken"] == [] and report["cited"] == ["PMID111"]


def test_gate_blocks_unverified_entry(tmp_path):
    """A cited entry present in the library but not verified must also fail closed."""
    _write_lib(tmp_path, verified=False)
    with pytest.raises(ReferenceIntegrityError):
        gate_run_dir(tmp_path, normalize_pmid_citations("Finding [PMID: 111]."))


def test_gate_dormant_without_references_json(tmp_path):
    """No references.json -> gate is a no-op (opt-in), even citing a ghost key."""
    assert gate_run_dir(tmp_path, "[PMID999] [GHOST]") is None


def test_numbered_bibliography_renders_when_references_present():
    lib = {"PMID111": {"authors": "Smith J", "title": "A trial of X", "journal": "J Onc",
                       "year": 2024, "volume": "12", "pages": "1-9", "pmid": "111",
                       "doi": "", "verified": True}}
    narrative = {
        "introduction": "Rationale established [PMID: 111].",
        "abstract_background": "Background [PMID: 111].",
        "abstract_results": "ORR was 84% [PMID: 111].",
        "abstract_conclusions": "Cautious [PMID: 111].",
        "discussion": "Consistent with prior work [PMID: 111].",
    }
    comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS,
                             narrative=narrative, references=lib)
    # In-text citations renumbered to [1]; the prose paragraph carries no residual
    # [PMID: n] token or bare key (the boilerplate disclaimer note is not prose).
    intro = next(c for c in comps if c.key == "01_introduction")
    prose = next(b for b in intro.blocks
                 if isinstance(b, Paragraph) and "Rationale" in b.text)
    assert "[1]" in prose.text and "[PMID" not in prose.text
    # References section is a numbered Vancouver list.
    refs = next(c for c in comps if c.key == "10_references")
    bib = next(b for b in refs.blocks if isinstance(b, BulletList))
    assert any(item.startswith("1.") and "A trial of X" in item for item in bib.items)


def test_no_references_json_preserves_legacy_prose_and_reflist():
    """Additive invariant: without references, prose keeps [PMID: n] and the reference
    section stays the included-studies bullet list."""
    narrative = {"introduction": "Rationale [PMID: 111].", "abstract_background": "",
                 "abstract_results": "", "abstract_conclusions": "", "discussion": ""}
    comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS, narrative=narrative)
    intro = next(c for c in comps if c.key == "01_introduction")
    assert any(isinstance(b, Paragraph) and "[PMID: 111]" in b.text for b in intro.blocks)
    refs = next(c for c in comps if c.key == "10_references")
    assert refs.title == "References (Included Studies)"
