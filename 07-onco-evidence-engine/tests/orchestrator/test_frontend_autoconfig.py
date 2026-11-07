"""WP-M3.5 — autoconfig + promote + PROSPERO freeze."""
import json

from core.sr.orchestrator import frontend
from core.sr.orchestrator.frontend import PicoDraft
from core.sr.orchestrator.stages import RunContext

TS = "2026-06-16T00:00:00Z"


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _write_pico(tmp_path):
    (tmp_path / "pico.json").write_text(PicoDraft(
        framing="PICO", population="RRMM after 1-3 prior lines",
        intervention="BCMA CAR-T", comparator="bispecific antibody",
        outcomes=["Overall survival", "PFS"], inclusion_criteria=["RCT"],
        exclusion_criteria=["preclinical"]).model_dump_json())


def test_autoconfig_generates_and_validates_vertical(tmp_path):
    _write_pico(tmp_path)
    res = frontend.autoconfig_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    gen = tmp_path / "generated_vertical"
    for f in ("pico.yaml", "search_strategies.yaml", "extraction_schema.yaml"):
        assert (gen / f).exists()
    assert res.summary["validated"] is True

    # the generated vertical loads through the real config schema
    from core.sr.config import SRConfig
    cfg = SRConfig(gen)
    assert "BCMA CAR-T" in cfg.review_title
    assert cfg.inclusion_criteria == ["RCT"] and cfg.exclusion_criteria == ["preclinical"]
    # extraction schema is runnable (group->fields mapping), not just superficially loadable
    fields = cfg.extraction_fields()
    assert {f["name"] for f in fields} >= {"Overall survival", "PFS"}
    assert all(f["group"] == "efficacy" for f in fields)


def test_promote_rejects_path_traversal_slug(tmp_path):
    import pytest
    _write_pico(tmp_path)
    frontend.autoconfig_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    for bad in ("../evil", "a/b", "..", "/abs"):
        with pytest.raises(ValueError):
            frontend.promote_protocol(tmp_path, bad, ts_utc=TS, verticals_dir=tmp_path / "verticals")


def test_promote_copies_and_writes_freeze_hash(tmp_path):
    _write_pico(tmp_path)
    frontend.autoconfig_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    vdir = tmp_path / "verticals"
    freeze = frontend.promote_protocol(tmp_path, "my-review", ts_utc=TS, verticals_dir=vdir)
    assert (vdir / "my-review" / "pico.yaml").exists()
    assert len(freeze["freeze_hash"]) == 64 and freeze["slug"] == "my-review"
    saved = json.loads((tmp_path / "protocol_freeze.json").read_text())
    assert saved["freeze_hash"] == freeze["freeze_hash"]      # PROSPERO pre-registration artifact


def test_record_deviation_appends(tmp_path):
    frontend.record_deviation(tmp_path, field="eligibility.exclusion",
                              old=["preclinical"], new=["preclinical", "n<10"],
                              ts_utc=TS, reason="add small-study exclusion post-freeze")
    frontend.record_deviation(tmp_path, field="outcomes", old=["OS"], new=["OS", "ORR"],
                              ts_utc=TS)
    devs = json.loads((tmp_path / "protocol_deviations.json").read_text())
    assert len(devs) == 2 and devs[0]["field"] == "eligibility.exclusion"


def test_promote_fails_without_autoconfig(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        frontend.promote_protocol(tmp_path, "x", ts_utc=TS, verticals_dir=tmp_path / "v")
