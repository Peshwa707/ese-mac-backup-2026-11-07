"""The product-shell /ui/calibration endpoint that feeds the calibration dashboard."""
from __future__ import annotations

from fastapi.testclient import TestClient

from web.server import app


def test_ui_calibration_serves_calibration_json():
    client = TestClient(app)
    r = client.get("/ui/calibration")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["source"] == "fixture:real_run_min"
    assert data["n_positive"] == 4 and data["n_negative"] == 46
    assert data["target_met"] is True
    assert data["achieved_sensitivity"] >= 0.99
    assert 0.0 <= data["auc"] <= 1.0
    assert data["curve"]                       # non-empty sweep for the chart
    assert data["chosen_threshold"] is not None
