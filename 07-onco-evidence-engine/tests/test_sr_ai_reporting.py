"""Tests for the RAISE / PRISMA-trAIce AI-use statement."""

from __future__ import annotations

from core.sr.ai_reporting import build_ai_reporting
from core.sr.config import load_sr_config
from core.sr.manuscript.assemble import build_components
from core.sr.manuscript.model import Table
from core.sr.profile import load_profile
from tests.test_sr_manuscript import _RECORDS, _synthetic_run


def test_build_ai_reporting_uses_audit_evidence():
    audit = {"resolution": {"resolved": 61, "checked": 61},
             "anchoring": {"anchored": 560, "substantive_values": 703,
                           "anchored_rate": 0.797, "estimated_for_human_review": 33, "nr": 850}}
    r = build_ai_reporting(_synthetic_run(), audit, load_profile("systematic"), "2026-06-06")
    assert any(s["ai"] == "Yes" for s in r["stages"])      # screening etc.
    assert any(s["ai"] == "No" for s in r["stages"])        # search/GRADE/meta deterministic
    assert any("61/61" in v for v in r["validation"])
    assert any("quote-anchored" in v for v in r["validation"])


def test_ai_statement_always_present_in_manuscript():
    for prof in ("systematic", "scoping"):
        comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS,
                                 profile=load_profile(prof), day="2026-06-06")
        ai = next((c for c in comps if c.key == "13_ai_reporting"), None)
        assert ai is not None
        assert any(isinstance(b, Table) for b in ai.blocks)   # the stage table
