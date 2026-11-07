"""Tests for risk-of-bias tool selection, prompt building, normalize, and figure."""

from __future__ import annotations

from core.sr import rob
from core.sr.models import RoBAssessment, RoBDomain
from core.sr.rob_figure import traffic_light_svg


def test_tool_selected_by_design():
    assert rob.select_tool("RCT") == "rob2"
    assert rob.select_tool("randomized") == "rob2"
    assert rob.select_tool("cohort") == "robins_i"
    assert rob.select_tool("registry") == "robins_i"
    assert rob.select_tool("single-arm") == "nos"
    assert rob.select_tool("something weird") == "nos"   # conservative default


def test_build_prompt_includes_domains_and_text():
    tool, system, user = rob.build_prompt("111", "RCT", "Patients were randomly assigned...")
    assert tool == "rob2"
    assert "randomization" in user.lower()
    assert "<study>" in user and "randomly assigned" in user
    assert "methodologist" in system.lower()


def test_normalize_fills_missing_domains():
    a = RoBAssessment(pmid="1", tool="rob2",
                      domains=[RoBDomain(key="D1", judgment="Low")])
    rob.normalize(a)
    keys = {d.key for d in a.domains}
    assert keys == {"D1", "D2", "D3", "D4", "D5"}
    backfilled = next(d for d in a.domains if d.key == "D3")
    assert backfilled.judgment == "No information" and backfilled.label


def test_traffic_light_svg_renders_circles():
    a = RoBAssessment(pmid="111", tool="rob2", overall="Low",
                      domains=[RoBDomain(key="D1", judgment="Low"),
                               RoBDomain(key="D2", judgment="High")])
    svg = traffic_light_svg([a])
    assert svg.startswith("<svg") and "</svg>" in svg
    assert "#3a8a3a" in svg and "#b34a4a" in svg     # green + red present
    assert "111" in svg


def test_assemble_uses_real_rob_component_when_provided():
    from core.sr.config import load_sr_config
    from core.sr.manuscript.assemble import build_components
    from tests.test_sr_manuscript import _RECORDS, _synthetic_run

    rob_list = [RoBAssessment(pmid="111", tool="rob2", overall="Some concerns",
                              domains=[RoBDomain(key="D1", judgment="Low")])]
    comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS, rob=rob_list)
    rc = next(c for c in comps if c.key == "06_risk_of_bias")
    # Not a stub: contains a per-study table row with the PMID.
    from core.sr.manuscript.model import Table
    assert any(isinstance(b, Table) and any("111" in str(cell) for row in b.rows for cell in row)
               for b in rc.blocks)
