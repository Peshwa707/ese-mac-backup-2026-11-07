"""Tests for the scoping-review capability (charting + PCC framing)."""

from __future__ import annotations

from core.sr.config import load_sr_config
from core.sr.manuscript.assemble import build_components
from core.sr.manuscript.model import BulletList, Table
from core.sr.profile import load_profile
from tests.test_sr_manuscript import _RECORDS, _synthetic_run


def test_pcc_text_present():
    cfg = load_sr_config()
    pcc = cfg.pcc_text()
    assert "Concept:" in pcc and "Context:" in pcc
    assert cfg.framing_text("pcc") == pcc
    assert cfg.framing_text("pico") == cfg.pico_text()


def test_scoping_has_evidence_map_and_pcc_framing():
    comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS,
                             profile=load_profile("scoping"))
    keys = [c.key for c in comps]
    assert "06_evidence_map" in keys and "06_risk_of_bias" not in keys
    # Evidence map has charting tables.
    emap = next(c for c in comps if c.key == "06_evidence_map")
    assert sum(isinstance(b, Table) for b in emap.blocks) >= 3
    # Introduction uses PCC framing.
    intro = next(c for c in comps if c.key == "01_introduction")
    pcc_bullets = next(b for b in intro.blocks if isinstance(b, BulletList))
    assert any("Concept:" in i for i in pcc_bullets.items)


def test_systematic_has_no_evidence_map():
    comps = build_components(_synthetic_run(), load_sr_config(), _RECORDS,
                             profile=load_profile("systematic"))
    assert "06_evidence_map" not in [c.key for c in comps]
