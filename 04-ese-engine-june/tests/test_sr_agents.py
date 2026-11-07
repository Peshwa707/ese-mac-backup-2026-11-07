"""Tests for the Phase-0 consistency backbone (stage specs + runner validate/repair)."""

from __future__ import annotations

import json

from core.sr.agents import runner
from core.sr.agents.spec import EXTRACTION, SCREENING, STAGES
from core.sr.config import load_sr_config


def test_screening_prompt_renders_criteria_from_config():
    cfg = load_sr_config()
    text = SCREENING.render_prompt(cfg)
    assert "{{PICO}}" not in text and "{{INCLUDE}}" not in text and "{{EXCLUDE}}" not in text
    assert "Population:" in text                     # PICO filled
    assert "BCMA-directed" in text                   # criteria filled
    assert SCREENING.json_schema()["title"] == "ScreeningResult"


def test_extraction_prompt_renders_fields():
    cfg = load_sr_config()
    text = EXTRACTION.render_prompt(cfg)
    assert "{{FIELDS}}" not in text and "{{RULES}}" not in text
    assert "[efficacy] orr" in text                  # field list filled from yaml


def test_prepare_batches_splits_and_writes_prompt(tmp_path):
    cfg = load_sr_config()
    items = [{"pmid": str(i)} for i in range(65)]
    batches = runner.prepare_batches(SCREENING, items, tmp_path, cfg=cfg)
    assert len(batches) == 3                          # 30 + 30 + 5
    assert all(b.exists() for b in batches)
    assert (tmp_path / "screening_prompt.md").exists()
    first = json.loads(batches[0].read_text())
    assert len(first) == 30


def test_collect_validates_and_reports_repairs(tmp_path):
    # 111 valid; 222 schema-invalid (bad decision); 333 never produced.
    (tmp_path / "screen_batch_000.json").write_text(json.dumps([
        {"pmid": "111", "decision": "include", "reason": "ok", "confidence": 0.9},
        {"pmid": "222", "decision": "maybe", "reason": "bad enum", "confidence": 0.5},
    ]))
    res = runner.collect(SCREENING, tmp_path, expected_ids=["111", "222", "333"])
    assert [v.pmid for v in res.valid] == ["111"]
    assert "222" in res.missing and "333" in res.missing
    assert res.ok is False


def test_collect_ok_when_all_valid(tmp_path):
    (tmp_path / "screen_batch_000.json").write_text(json.dumps([
        {"pmid": "111", "decision": "exclude", "reason": "review", "confidence": 0.8},
    ]))
    res = runner.collect(SCREENING, tmp_path, expected_ids=["111"])
    assert res.ok and len(res.valid) == 1


def test_registry_has_canonical_stages():
    assert set(STAGES) == {"screening", "extraction"}
    assert SCREENING.batch_size == 30 and EXTRACTION.batch_size == 1
