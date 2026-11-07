"""WP-M3.3 — feasibility_scope front-end stage."""
import json

from core.sr.orchestrator import frontend
from core.sr.orchestrator.frontend import FeasibilityAssessment, PicoDraft
from core.sr.orchestrator.stages import RunContext


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _write_pico(tmp_path):
    (tmp_path / "pico.json").write_text(PicoDraft(
        population="RRMM 1-3 lines", intervention="BCMA CAR-T",
        comparator="bispecific", outcomes=["PFS", "OS"]).model_dump_json())


def test_feasibility_reports_count_and_nma(tmp_path, monkeypatch):
    _write_pico(tmp_path)

    seen = {}

    async def _records(query, **k):
        seen["query"] = query
        return [{"pmid": str(i), "title": f"trial {i}"} for i in range(7)]

    monkeypatch.setattr(frontend, "_verified_records", _records)
    monkeypatch.setattr(frontend, "gateway_parse",
                        lambda *a, **k: FeasibilityAssessment(
                            nma_connected=True, network_rationale="shared SoC anchor",
                            recommendation="proceed"))
    res = frontend.feasibility_scope_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    rep = json.loads((tmp_path / "feasibility_report.json").read_text())
    assert rep["estimated_primary_studies"] == 7
    assert rep["nma_connected"] is True and rep["recommendation"] == "proceed"
    assert "NOT an exhaustive" in rep["estimate_method"]          # honest scoping label
    assert "advisory" in rep["nma_assessment_method"].lower()     # NMA judgment labeled advisory
    assert res.summary["estimated_primary_studies"] == 7
    assert "pico.json" in res.deterministic_inputs
    # PICO terms are quoted as phrases (no raw operator injection into PubMed syntax)
    assert '"RRMM 1-3 lines"' in seen["query"] and '"BCMA CAR-T"' in seen["query"]


def test_pubmed_phrase_neutralizes_operators():
    out = frontend._pubmed_phrase('cancer[majr] OR "x"')
    assert out.startswith('"') and out.endswith('"')
    assert "[" not in out and out.count('"') == 2            # field tags + inner quotes stripped


def test_feasibility_inherits_fail_closed_on_outage(tmp_path, monkeypatch):
    _write_pico(tmp_path)
    import pytest

    async def _down(query, **k):
        raise RuntimeError("PubMed search unavailable")

    monkeypatch.setattr(frontend, "_verified_records", _down)
    with pytest.raises(RuntimeError, match="unavailable"):
        frontend.feasibility_scope_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
