"""The /ui/meta-analysis loader + endpoint that feed the meta-analysis dashboard."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from web.meta_analysis import figure_path, load_meta_analysis
from web.server import app


def _seed(base):
    (base / "pooled_results.json").write_text(json.dumps({
        "proportions": [{"label": "ORR", "k": 2,
                         "results": [{"group": "ALL", "prop": 82.0, "ci": [77.2, 86.4], "k": 2, "I2": 86.6}]}],
        "hazard_ratios": [],
    }))
    (base / "indirect_comparison_results.json").write_text(json.dumps({"bucher_indirect_PFS": {}}))
    (base / "nma_league_table.json").write_text(json.dumps({"analysis": "star network"}))
    figs = base / "figures"
    figs.mkdir()
    (figs / "forest_orr.png").write_bytes(b"\x89PNG\r\n")


def test_load_meta_analysis_normalizes(tmp_path):
    _seed(tmp_path)
    data = load_meta_analysis(base=tmp_path)
    assert data["available"] is True
    assert data["proportions"][0]["label"] == "ORR"
    assert data["proportions"][0]["results"][0]["group"] == "ALL"
    assert data["nma"]["analysis"] == "star network"
    assert "forest_orr.png" in data["figures"]


def test_load_meta_analysis_missing(tmp_path):
    data = load_meta_analysis(base=tmp_path)        # empty dir
    assert data["available"] is False
    assert "reason" in data


def test_figure_path_rejects_traversal_and_resolves(tmp_path):
    _seed(tmp_path)
    assert figure_path("forest_orr.png", base=tmp_path) is not None
    assert figure_path("../pooled_results.json", base=tmp_path) is None   # traversal blocked
    assert figure_path("missing.png", base=tmp_path) is None
    assert figure_path("evil.svg", base=tmp_path) is None                 # non-png rejected


def test_endpoint_contract():
    # Tolerant: returns available True (real run present) or False (absent); always has the key.
    r = TestClient(app).get("/ui/meta-analysis")
    assert r.status_code == 200
    assert "available" in r.json()
