"""Tests that the SR engine is domain-agnostic via vertical parameterization."""

from __future__ import annotations

from pathlib import Path

from core.sr.config import load_sr_config, shared_or_vertical, vertical_root


def test_load_a_non_oncology_vertical():
    cfg = load_sr_config("heart-failure-sglt2")
    assert "SGLT2" in cfg.review_title
    assert "HFrEF" in cfg.pico_text() or "ejection fraction" in cfg.pico_text().lower()
    # Its own extraction dictionary (not the oncology one).
    fields = {f["name"] for f in cfg.extraction_fields()}
    assert "cv_death_or_hhf_hr" in fields and "orr" not in fields


def test_shared_configs_fall_back_to_systematic_review():
    # The HF vertical supplies no rob/grade/prompts -> shared defaults are used.
    rob = shared_or_vertical("rob_config.yaml", "heart-failure-sglt2")
    assert rob.exists() and rob.parent.name == "systematic-review"
    prompt = shared_or_vertical("prompts/screening.md", "heart-failure-sglt2")
    assert prompt.exists() and "systematic-review" in str(prompt)


def test_env_selects_vertical(monkeypatch):
    monkeypatch.setenv("SR_VERTICAL", "heart-failure-sglt2")
    assert vertical_root().name == "heart-failure-sglt2"
    assert "SGLT2" in load_sr_config().review_title
    monkeypatch.delenv("SR_VERTICAL")


def test_poolable_fields_are_config_driven_per_vertical():
    # Heart-failure pools its OWN HR + safety outcomes (regression: previously the
    # stats engine hardcoded myeloma fields, so HF pooled nothing).
    hf = load_sr_config("heart-failure-sglt2").poolable_fields()
    hf_log = {f for f, _ in hf["log_effect"]}
    hf_prop = {f for f, _ in hf["proportion"]}
    assert {"cv_death_or_hhf_hr", "all_cause_mortality_hr"} <= hf_log
    assert "dka_pct" in hf_prop
    assert "orr" not in (hf_log | hf_prop)            # no myeloma leakage

    # The myeloma benchmark still pools its six proportion outcomes.
    mm = load_sr_config("mm-bcma-3l").poolable_fields()
    mm_prop = {f for f, _ in mm["proportion"]}
    assert {"complete_response_rate", "orr", "crs_any_grade"} <= mm_prop


def test_subgroup_axis_is_vertical_driven():
    from core.sr.stats.prepare import canonical_subgroup

    mm = load_sr_config("mm-bcma-3l")
    assert mm.subgroup_field == "modality" and mm.indirect_comparison is True
    assert canonical_subgroup("bispecific antibody", mm.subgroup_map) == "BsAb"

    # Non-oncology verticals are never coerced into CAR-T/BsAb buckets.
    hf = load_sr_config("heart-failure-sglt2")
    assert hf.subgroup_field == "" and hf.indirect_comparison is False
    assert canonical_subgroup("dapagliflozin", hf.subgroup_map) == "dapagliflozin"


def test_rob_grade_work_under_new_vertical(monkeypatch):
    # Tool selection / GRADE config load via shared fallback regardless of vertical.
    monkeypatch.setenv("SR_VERTICAL", "heart-failure-sglt2")
    from core.sr.rob import select_tool
    from core.sr.grade import grade_config
    assert select_tool("RCT") == "rob2"
    assert grade_config().get("levels")
    monkeypatch.delenv("SR_VERTICAL")
