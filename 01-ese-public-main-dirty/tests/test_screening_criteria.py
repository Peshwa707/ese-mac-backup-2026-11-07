"""Tests for the structured screening-criteria fold-in.

Pins the three safety properties the feature rests on:
  1. fail-closed on a malformed screening_criteria.yaml (never silently degrade),
  2. byte-compatible flat fallback for verticals without the file,
  3. fidelity — the structured render conveys every rule incl. the design tiers.
"""
from __future__ import annotations

import pytest
import yaml

from core.models import Citation
from core.sr.config import load_sr_config
from core.sr.screen import build_screening_prompt
from core.sr.screening_criteria import (
    CriteriaSet,
    load_for_vertical,
    render_criteria_set,
)

_GOOD = {
    "design_eligibility": {"eligible": ["rct"], "excluded": ["observational"]},
    "criteria": [{"id": "population", "kind": "include", "text": "adults"}],
}


def _write(tmp_path, data):
    (tmp_path / "screening_criteria.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- fail-closed
def test_absent_file_returns_none(tmp_path):
    assert load_for_vertical(tmp_path) is None  # no file -> flat fallback, not an error


def test_valid_loads(tmp_path):
    cs = load_for_vertical(_write(tmp_path, dict(_GOOD)))
    assert isinstance(cs, CriteriaSet) and cs.design_eligibility.eligible == ["rct"]


@pytest.mark.parametrize("mutate,label", [
    (lambda d: d.pop("design_eligibility"), "missing design_eligibility"),
    (lambda d: d["design_eligibility"].__setitem__("eligible", []), "empty eligible"),
    (lambda d: d.__setitem__("criteria", []), "empty criteria"),
    (lambda d: d["criteria"][0].__setitem__("near_miss", ["typo"]), "misspelled key"),
    (lambda d: d["criteria"].append({"id": "population", "kind": "exclude", "text": "dup"}), "duplicate id"),
])
def test_malformed_raises(tmp_path, mutate, label):
    d = {"design_eligibility": {"eligible": ["rct"], "excluded": ["observational"]},
         "criteria": [{"id": "population", "kind": "include", "text": "adults"}]}
    mutate(d)
    with pytest.raises((ValueError, Exception)):  # ValidationError is a ValueError subclass
        load_for_vertical(_write(tmp_path, d))


def test_non_dict_top_level_raises(tmp_path):
    (tmp_path / "screening_criteria.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_for_vertical(tmp_path)


def test_vertical_name_mismatch_raises(tmp_path):
    d = dict(_GOOD); d["vertical"] = "some-other-vertical"
    with pytest.raises(ValueError):
        load_for_vertical(_write(tmp_path, d))


# ---------------------------------------------------------------- flat fallback (backward-compat)
def _rec():
    return Citation(id="1", id_type="PMID", source="pubmed", title="t",
                    snippet="Double-blind RCT, oral clonidine vs placebo.")


def test_flat_fallback_for_vertical_without_yaml():
    # a vertical that ships no screening_criteria.yaml must screen EXACTLY as before
    cfg = load_sr_config("mm-bcma-3l")
    assert not (cfg.root / "screening_criteria.yaml").exists()
    _sys, user = build_screening_prompt([_rec()], cfg)
    assert "INCLUDE if ALL hold" in user and "CRITERION [" not in user


# ---------------------------------------------------------------- structured used + fidelity
def test_structured_prompt_for_clonidine():
    cfg = load_sr_config("clonidine-spine")
    _sys, user = build_screening_prompt([_rec()], cfg)
    assert "CRITERION [design]" in user and "Out of scope" in user
    assert "INCLUDE if ALL hold" not in user  # flat block replaced


def test_mmbcma_realworld_stays_eligible():
    # the load-bearing fix: a tiered NMA must keep real-world/registry ELIGIBLE
    cs = load_for_vertical(load_sr_config("mm-bcma-early-relapse").root)
    text = render_criteria_set(cs)
    assert "real_world_cohort" in text and "registry" in text
    assert "do NOT exclude it merely for being non-randomized" in text


def test_design_eligibility_always_rendered_even_with_explicit_design():
    # mm-bcma authors its own `design` criterion (bypasses synthesis); the machine-readable
    # eligible/excluded tiers must still reach the prompt.
    cs = load_for_vertical(load_sr_config("mm-bcma-early-relapse").root)
    text = render_criteria_set(cs)
    assert "STUDY-DESIGN ELIGIBILITY — eligible:" in text
    assert "real_world_cohort" in text.split("STUDY-DESIGN ELIGIBILITY")[1].split("\n")[0]
