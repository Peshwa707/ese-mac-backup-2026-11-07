"""WP-M3.4 — knowledge-based protocol critic (deterministic, no ML)."""
import json

from core.sr.orchestrator import protocol_critic as pc
from core.sr.orchestrator.frontend import PicoDraft
from core.sr.orchestrator.stages import RunContext


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _ids(findings):
    return {f.id for f in findings}


def test_weak_protocol_triggers_expected_rules():
    weak = PicoDraft(framing="PICO", population="MM", intervention="BCMA CAR-T",
                     comparator="", outcomes=["ORR", "PFS"],
                     inclusion_criteria=["adults"], exclusion_criteria=[])
    ids = _ids(pc.critique(weak))
    assert "PC002" in ids        # surrogate-only outcomes
    assert "PC003" in ids        # missing exclusion criteria
    assert "PC001" in ids        # empty comparator (PICO)
    assert "PC004" in ids        # short population ("MM")


def test_clean_protocol_has_no_blockers():
    clean = PicoDraft(framing="PICO", population="RRMM after 1-3 prior lines, lenalidomide-refractory",
                      intervention="BCMA CAR-T", comparator="bispecific antibody",
                      outcomes=["Overall survival", "PFS", "ORR"],
                      inclusion_criteria=["RCT or prospective cohort"],
                      exclusion_criteria=["preclinical", "n<10"])
    findings = pc.critique(clean)
    assert all(f.severity != "blocker" for f in findings)
    assert "PC002" not in _ids(findings)     # has OS -> not surrogate-only
    assert "PC006" in _ids(findings)         # comparator present -> transitivity reminder


def test_surrogate_detection_is_token_aware_not_substring():
    # "diagnosis"/"prognosis" contain "os" but are NOT overall-survival -> still surrogate-only
    p = PicoDraft(population="RRMM specific cohort", intervention="X", comparator="Y",
                  outcomes=["diagnosis response rate", "prognosis"],
                  inclusion_criteria=["a"], exclusion_criteria=["b"])
    assert "PC002" in _ids(pc.critique(p))         # not suppressed by the "os" in diagnosis
    # genuine OS / overall survival suppresses PC002
    for oc in ("OS", "Overall survival"):
        q = PicoDraft(population="RRMM specific cohort", intervention="X", comparator="Y",
                      outcomes=[oc, "ORR"], inclusion_criteria=["a"], exclusion_criteria=["b"])
        assert "PC002" not in _ids(pc.critique(q))


def test_progression_free_survival_is_surrogate_not_definitive():
    # "progression-free survival" contains "survival" but is a surrogate -> PC002 fires
    p = PicoDraft(population="RRMM specific cohort", intervention="X", comparator="Y",
                  outcomes=["progression-free survival", "ORR"],
                  inclusion_criteria=["a"], exclusion_criteria=["b"])
    assert "PC002" in _ids(pc.critique(p))
    # but a true overall-survival endpoint alongside it suppresses PC002
    q = PicoDraft(population="RRMM specific cohort", intervention="X", comparator="Y",
                  outcomes=["progression-free survival", "overall survival"],
                  inclusion_criteria=["a"], exclusion_criteria=["b"])
    assert "PC002" not in _ids(pc.critique(q))


def test_no_outcomes_is_a_blocker():
    p = PicoDraft(population="RRMM specific", intervention="X", comparator="Y",
                  outcomes=[], inclusion_criteria=["a"], exclusion_criteria=["b"])
    findings = pc.critique(p)
    assert any(f.id == "PC005" and f.severity == "blocker" for f in findings)


def test_adapter_writes_critique_with_citations(tmp_path):
    (tmp_path / "pico.json").write_text(PicoDraft(
        population="MM", intervention="CAR-T", comparator="", outcomes=["ORR"],
        inclusion_criteria=["a"], exclusion_criteria=[]).model_dump_json())
    res = pc.protocol_critic_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    rep = json.loads((tmp_path / "protocol_critique.json").read_text())
    assert rep["method"].startswith("knowledge-based")        # not ML
    assert rep["findings"] and all(f["citation"] for f in rep["findings"])  # every finding cited
    assert "pico.json" in res.deterministic_inputs
