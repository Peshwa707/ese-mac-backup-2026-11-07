"""WP7 — verified reference library + citation-integrity gate."""
import json

import pytest

from core.sr.references import (
    ReferenceIntegrityError,
    assert_export_ok,
    check_integrity,
    key_to_number,
    load_library,
    render_vancouver,
)

LIB = {
    "Smith2023": {"key": "Smith2023", "pmid": "111", "doi": "10.1/x", "verified": True,
                  "authors": "Smith J et al.", "title": "A trial", "journal": "NEJM",
                  "year": "2023", "volume": "388", "pages": "1-10"},
    "Doe2024": {"key": "Doe2024", "pmid": "222", "verified": True, "authors": "Doe A",
                "title": "Another", "journal": "Lancet", "year": "2024"},
}


def test_check_integrity_detects_broken_and_orphan():
    rep = check_integrity("As shown [Smith2023] and also [GHOST].", LIB)
    assert rep["cited"] == ["Smith2023"]
    assert rep["broken"] == ["GHOST"]
    assert rep["orphan"] == ["Doe2024"]


def test_status_flags_and_abbreviations_not_treated_as_citations():
    rep = check_integrity("result [PENDING] with HR [HR] and [CI] cited [Smith2023]", LIB)
    assert rep["broken"] == []        # PENDING/HR/CI are not citations


def test_pooled_subgroup_labels_not_treated_as_citations():
    # Round-2 finding #3: league-table / forest-plot prose uses bracketed subgroup labels
    # like [ALL]/[ANY]; these must not false-fail the export gate, while a real fabricated
    # key ([GHOST]) is still caught as broken.
    rep = check_integrity("Pooled across [ALL] patients and [ANY] subgroup [Smith2023]; [GHOST]", LIB)
    assert rep["broken"] == ["GHOST"]
    assert "ALL" not in rep["broken"] and "ANY" not in rep["broken"]


def test_render_vancouver_numbers_by_first_appearance():
    out = render_vancouver("see [Doe2024], then [Smith2023]", LIB)
    assert out[0].startswith("1. Doe A.")
    assert out[1].startswith("2. Smith J et al.")
    assert "PMID: 222" in out[0] and "doi:10.1/x" in out[1]


def test_key_to_number_map():
    assert key_to_number("[Smith2023] [Doe2024]", LIB) == {"Smith2023": 1, "Doe2024": 2}


def test_assert_export_ok_passes_clean_text():
    rep = assert_export_ok("cite [Smith2023]", LIB)
    assert rep["broken"] == []


def test_assert_export_ok_raises_on_broken_citation():
    with pytest.raises(ReferenceIntegrityError):
        assert_export_ok("cite [Smith2023] and [GHOST]", LIB)


def test_assert_export_ok_raises_on_unverified_cited_reference():
    lib = {"Bad2025": {"key": "Bad2025", "verified": False, "authors": "X", "title": "Y",
                       "journal": "Z", "year": "2025"}}
    with pytest.raises(ReferenceIntegrityError):
        assert_export_ok("cite [Bad2025]", lib)            # cited but unverified -> fail closed
    assert_export_ok("cite [Bad2025]", lib, require_verified=False)  # opt-out allowed


def test_load_library_absent_returns_empty(tmp_path):
    assert load_library(tmp_path / "nope.json") == {}


def test_load_library_reads_json(tmp_path):
    p = tmp_path / "references.json"
    p.write_text(json.dumps(LIB))
    assert set(load_library(p)) == {"Smith2023", "Doe2024"}


def test_manuscript_tool_path_applies_reference_gate(tmp_path, monkeypatch):
    """The registered `manuscript` tool path (not just `python -m`) must fail closed
    on a broken key citation before exporting (holistic-review finding)."""
    import core.tools.builtin as builtin
    from core.models import Citation, IdType, RetrievalResult
    from core.sr.agent_io import dump_corpus, records_for_agents
    from core.sr.models import ScreeningResult, ScreenDecision
    from core.sr.pipeline import Corpus
    from core.sr.config import load_sr_config

    cfg = load_sr_config("mm-bcma-3l")
    cites = [Citation(id_type=IdType.PMID, id="111", source="pubmed", title="A trial",
                      snippet="abstract", verified=True, verification="live")]
    corpus = Corpus(cfg=cfg, run_id="t", search_result=RetrievalResult(source="pubmed", query="q", citations=cites),
                    identified=cites, deduped=cites, verified=cites, rejected=[], seed_verification={},
                    per_source={"pubmed": 1}, sources_executed=["pubmed"])
    dump_corpus(corpus, tmp_path / "corpus.json")
    (tmp_path / "records_for_agents.json").write_text(json.dumps(records_for_agents(corpus)))
    (tmp_path / "screening_merged.json").write_text(json.dumps([
        ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE, reason="x", confidence=0.9).model_dump(mode="json")]))
    (tmp_path / "extractions.json").write_text("[]")
    (tmp_path / "narrative_final.json").write_text(json.dumps({"results": "as shown [GHOST]"}))
    (tmp_path / "references.json").write_text("{}")   # present -> gate active; [GHOST] is broken

    # The gate runs right after reading narrative_final.json, before export -> must raise.
    with pytest.raises(ReferenceIntegrityError):
        builtin._run_manuscript(tmp_path, {"vertical": "mm-bcma-3l"}, "harness")


def test_gate_run_dir_noop_when_no_library(tmp_path):
    from core.sr.references import gate_run_dir
    assert gate_run_dir(tmp_path, "cite [Anything]") is None   # no references.json -> unaffected


def test_gate_run_dir_empty_present_library_still_fails_closed(tmp_path):
    from core.sr.references import gate_run_dir
    (tmp_path / "references.json").write_text("{}")        # present but empty
    with pytest.raises(ReferenceIntegrityError):
        gate_run_dir(tmp_path, "cite [Smith2023]")         # any [key] is broken vs empty lib
    assert gate_run_dir(tmp_path, "no citations here")["broken"] == []   # clean text ok


def test_gate_run_dir_fails_closed_on_broken_citation(tmp_path):
    from core.sr.references import gate_run_dir
    (tmp_path / "references.json").write_text(json.dumps(LIB))
    with pytest.raises(ReferenceIntegrityError):
        gate_run_dir(tmp_path, "cite [Smith2023] and [GHOST]")
    # clean text passes
    assert gate_run_dir(tmp_path, "cite [Smith2023]")["broken"] == []
