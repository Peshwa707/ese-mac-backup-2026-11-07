"""WP-M3.1 — idea_to_pico front-end stage."""
import json

import pytest

from core.sr.orchestrator import frontend
from core.sr.orchestrator.frontend import PicoDraft
from core.sr.orchestrator.stages import RunContext


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _stub(monkeypatch, draft):
    captured = {}

    def _fake(schema, system, user, *, stage, spec, content_class="abstract", max_tokens=16000):
        captured.update(stage=stage, content_class=content_class, user=user, model=spec.model)
        return draft

    monkeypatch.setattr("core.sr.orchestrator.frontend.gateway_parse", _fake)
    return captured


def test_idea_to_pico_writes_structured_pico(tmp_path, monkeypatch):
    (tmp_path / "idea.txt").write_text("BCMA CAR-T vs bispecifics in early-relapse myeloma")
    draft = PicoDraft(framing="PICO", population="RRMM 1-3 prior lines",
                      intervention="BCMA CAR-T", comparator="bispecific antibody",
                      outcomes=["PFS", "ORR"], inclusion_criteria=["RCT or cohort"],
                      exclusion_criteria=["preclinical"])
    cap = _stub(monkeypatch, draft)
    res = frontend.idea_to_pico_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    assert (tmp_path / "pico.json").exists()
    pico = json.loads((tmp_path / "pico.json").read_text())
    assert pico["population"] == "RRMM 1-3 prior lines" and pico["outcomes"] == ["PFS", "ORR"]
    assert res.summary["framing"] == "PICO" and res.summary["n_outcomes"] == 2
    assert "idea.txt" in res.deterministic_inputs
    # metadata-class egress (no clinical full text leaves here)
    assert cap["content_class"] == "metadata" and cap["stage"] == "idea_to_pico"


def test_idea_to_pico_fails_closed_without_idea(tmp_path, monkeypatch):
    _stub(monkeypatch, PicoDraft(population="x", intervention="y"))
    with pytest.raises(FileNotFoundError):
        frontend.idea_to_pico_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))


def test_idea_to_pico_fails_closed_on_empty_idea(tmp_path, monkeypatch):
    (tmp_path / "idea.txt").write_text("   \n")
    _stub(monkeypatch, PicoDraft(population="x", intervention="y"))
    with pytest.raises(ValueError, match="empty"):
        frontend.idea_to_pico_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
