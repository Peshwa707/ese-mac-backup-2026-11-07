"""Phase 5 Wave B — boundary + prompt-safety tests (offline, no API key).

Covers:
- LLM system prompts now come from the vertical config (H1/H2).
- Prompt-injection delimiters wrap external text in the constructed prompts.
- The connector SELECTION is config-driven (boundary C4).
- Conflict context is preserved in provenance when a contesting finding is
  dropped downstream (AH F8).
"""

from __future__ import annotations

import core.synthesis.synthesize as synthesize
import core.synthesis.conflict as conflict
import core.synthesis.claim_checker as claim_checker
import core.grounding.structure as structure
from core.config.vertical import load_vertical
from core.connectors import connectors_for_vertical
from core.models import Citation, ClaimCheck, Finding, IdType

_VERTICAL = load_vertical("oncology")


def _cite(cid: str, snippet: str = "snippet text") -> Citation:
    return Citation(id_type=IdType.PMID, id=cid, source="pubmed", snippet=snippet)


def _finding(stmt: str, cid: str = "1", conflicts=None) -> Finding:
    return Finding(
        statement=stmt,
        citations=[_cite(cid)],
        quote="snippet text",
        conflicts_with=conflicts or [],
    )


class _Captured:
    """Records the (system, user) passed to llm.parse and returns a canned obj."""

    def __init__(self, return_value):
        self.system = None
        self.user = None
        self._rv = return_value

    def __call__(self, schema, system, user, **kwargs):
        self.system = system
        self.user = user
        return self._rv


# --------------------------------------------------------------------------- #
# (a) System prompts are read from the vertical config.
# --------------------------------------------------------------------------- #

def test_vertical_supplies_all_prompts():
    for key in ("structure", "synthesis", "manuscript", "conflict", "claim_check"):
        assert _VERTICAL.prompt(key), f"vertical missing prompt: {key}"


def test_vertical_prompt_falls_back_to_default():
    # An unknown key yields the supplied generic fallback, not an error.
    assert _VERTICAL.prompt("does_not_exist", "GENERIC FALLBACK") == "GENERIC FALLBACK"


def test_structure_uses_vertical_prompt_and_case_delimiter(monkeypatch):
    cap = _Captured(_StubModelDump({}))
    monkeypatch.setattr(structure.llm, "parse", cap)
    structure.structure_case("PATIENT CASE TEXT", _VERTICAL)
    assert cap.system == _VERTICAL.prompt("structure")
    assert "<case>" in cap.user and "</case>" in cap.user
    assert "PATIENT CASE TEXT" in cap.user


def test_synthesis_uses_vertical_prompt_and_snippet_delimiter(monkeypatch):
    cap = _Captured(synthesize._SynthesisOutput(findings=[]))
    monkeypatch.setattr(synthesize.llm, "parse", cap)
    synthesize.synthesize_findings({}, [_cite("111")], _VERTICAL)
    assert cap.system == _VERTICAL.prompt("synthesis")
    assert "<snippets>" in cap.user and "</snippets>" in cap.user


def test_manuscript_uses_vertical_prompt_and_findings_delimiter(monkeypatch):
    cap = _Captured(synthesize._Manuscript())
    monkeypatch.setattr(synthesize.llm, "parse", cap)
    synthesize.generate_manuscript({}, [_finding("a claim")], _VERTICAL)
    assert cap.system == _VERTICAL.prompt("manuscript")
    assert "<findings>" in cap.user and "</findings>" in cap.user


def test_conflict_uses_vertical_prompt_and_findings_delimiter(monkeypatch):
    cap = _Captured(conflict._Conflicts(pairs=[]))
    monkeypatch.setattr(conflict.llm, "parse", cap)
    conflict.detect_conflicts([_finding("a"), _finding("b")], _VERTICAL)
    assert cap.system == _VERTICAL.prompt("conflict")
    assert "<findings>" in cap.user and "</findings>" in cap.user


def test_claim_check_uses_vertical_prompt_and_claim_source_delimiters(monkeypatch):
    cap = _Captured(ClaimCheck(finding_index=0, supported=True))
    monkeypatch.setattr(claim_checker.llm, "parse", cap)
    claim_checker.check_finding(0, _finding("a claim", "1"), _VERTICAL)
    assert cap.system == _VERTICAL.prompt("claim_check")
    assert "<claim>" in cap.user and "</claim>" in cap.user
    assert "<sources>" in cap.user and "</sources>" in cap.user


def test_system_prompts_instruct_data_not_instructions():
    # Every vertical prompt must tell the model delimited content is DATA.
    for key in ("structure", "synthesis", "manuscript", "conflict", "claim_check"):
        text = _VERTICAL.prompt(key).lower()
        assert "data" in text and "instruction" in text, key


# --------------------------------------------------------------------------- #
# (b) Connector selection is config-driven.
# --------------------------------------------------------------------------- #

def test_connectors_for_vertical_returns_expected_four():
    conns = connectors_for_vertical(_VERTICAL)
    assert [c.source for c in conns] == ["pubmed", "clinicaltrials", "openfda", "pdq"]


def test_connector_sources_come_from_config():
    assert _VERTICAL.connector_sources == ("pubmed", "clinicaltrials", "openfda", "pdq")


# --------------------------------------------------------------------------- #
# (d) Conflict context is preserved in provenance (AH F8).
# --------------------------------------------------------------------------- #

def test_lost_conflict_recorded_when_counterpart_dropped():
    # f0 (kept) conflicted with f1 (dropped). Survivor must leave an audit trail.
    f0 = _finding("survivor", conflicts=[1])
    f1 = _finding("contester", conflicts=[0])
    records = conflict.lost_conflict_records([f0, f1], {"survivor"})
    assert len(records) == 1
    assert records[0]["reason"] == "conflict_context_lost"
    assert records[0]["statement"] == "survivor"
    assert "contester" in records[0]["detail"]


def test_no_lost_conflict_when_both_survive():
    f0 = _finding("a", conflicts=[1])
    f1 = _finding("b", conflicts=[0])
    records = conflict.lost_conflict_records([f0, f1], {"a", "b"})
    assert records == []


class _StubModelDump:
    """Mimics a pydantic-ish object exposing model_dump (for structure_case)."""

    def __init__(self, data: dict):
        self._data = data

    def model_dump(self):
        return self._data
