"""Offline tests for the adversarial claim-checker (no API key needed).

We patch ``claim_checker.check_finding`` (the only LLM-touching call) so the
filter logic — keep supported, drop unsupported, fail CLOSED on error, and
remap ``conflicts_with`` after any drop — is exercised deterministically.
"""

from __future__ import annotations

from core.config.vertical import load_vertical
from core.synthesis import claim_checker
from core.models import Citation, ClaimCheck, Finding, IdType

# Real vertical config (offline — just reads YAML). filter_supported now threads
# the vertical through to check_finding so the system prompt comes from config.
_VERTICAL = load_vertical("oncology")


def _cite(cid: str) -> Citation:
    return Citation(id_type=IdType.PMID, id=cid, source="pubmed", snippet="snippet text")


def _finding(stmt: str, cid: str, conflicts=None) -> Finding:
    return Finding(
        statement=stmt,
        citations=[_cite(cid)],
        quote="snippet text",
        conflicts_with=conflicts or [],
    )


def test_unsupported_finding_is_dropped(monkeypatch):
    f = _finding("unsupported claim", "1")
    monkeypatch.setattr(
        claim_checker,
        "check_finding",
        lambda i, fdg, v: ClaimCheck(finding_index=i, supported=False, notes="no overlap"),
    )
    kept, dropped = claim_checker.filter_supported([f], _VERTICAL)
    assert kept == []
    assert dropped[0]["reason"] == "claim_unsupported"


def test_supported_finding_is_kept(monkeypatch):
    f = _finding("supported claim", "1")
    monkeypatch.setattr(
        claim_checker,
        "check_finding",
        lambda i, fdg, v: ClaimCheck(finding_index=i, supported=True),
    )
    kept, dropped = claim_checker.filter_supported([f], _VERTICAL)
    assert [x.statement for x in kept] == ["supported claim"]
    assert dropped == []


def test_check_exception_fails_closed(monkeypatch):
    f = _finding("unverifiable claim", "1")

    def _boom(i, fdg, v):
        raise RuntimeError("LLM returned no parseable structured output")

    monkeypatch.setattr(claim_checker, "check_finding", _boom)
    kept, dropped = claim_checker.filter_supported([f], _VERTICAL)
    # Fail CLOSED: an unverifiable finding is treated as unsupported and dropped.
    assert kept == []
    assert dropped[0]["reason"] == "claim_check_failed"
    assert "no parseable" in dropped[0]["notes"]


def test_conflicts_remapped_after_drop(monkeypatch):
    # f0 kept, f1 dropped (exception), f2 kept; f0 conflicts with old idx 2 -> 1.
    f0 = _finding("keep0", "1", conflicts=[2])
    f1 = _finding("drop1", "2")
    f2 = _finding("keep2", "3", conflicts=[0])

    def _check(i, fdg, v):
        if i == 1:
            raise RuntimeError("boom")
        return ClaimCheck(finding_index=i, supported=True)

    monkeypatch.setattr(claim_checker, "check_finding", _check)
    kept, dropped = claim_checker.filter_supported([f0, f1, f2], _VERTICAL)
    assert [x.statement for x in kept] == ["keep0", "keep2"]
    assert kept[0].conflicts_with == [1]
    assert kept[1].conflicts_with == [0]
    assert any(d["reason"] == "claim_check_failed" for d in dropped)


def test_one_failure_does_not_abort_job(monkeypatch):
    f0 = _finding("ok", "1")
    f1 = _finding("explodes", "2")

    def _check(i, fdg, v):
        if fdg.statement == "explodes":
            raise RuntimeError("kaboom")
        return ClaimCheck(finding_index=i, supported=True)

    monkeypatch.setattr(claim_checker, "check_finding", _check)
    kept, dropped = claim_checker.filter_supported([f0, f1], _VERTICAL)
    # The whole job did not abort; the good finding survived.
    assert [x.statement for x in kept] == ["ok"]
    assert len(dropped) == 1
