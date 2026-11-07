"""Tests for living-review delta detection and screening merge."""

from __future__ import annotations

import json

from core.sr.living import (
    compute_delta,
    delta_summary,
    merge_screening,
    previous_screened_pmids,
)


def test_compute_delta_splits_new_vs_seen():
    new_records = [{"pmid": "1"}, {"pmid": "2"}, {"pmid": "3"}]
    delta = compute_delta(new_records, seen_pmids={"1"})
    assert [r["pmid"] for r in delta.new_records] == ["2", "3"]
    assert delta.n_new == 2 and delta.n_carried == 1
    assert delta_summary(delta) == {"new_this_cycle": 2, "carried_forward": 1, "total": 3}


def test_merge_screening_new_wins_on_overlap():
    prev = [{"pmid": "1", "decision": "include"}, {"pmid": "2", "decision": "exclude"}]
    new = [{"pmid": "2", "decision": "include"}, {"pmid": "3", "decision": "uncertain"}]
    merged = {d["pmid"]: d["decision"] for d in merge_screening(prev, new)}
    assert merged == {"1": "include", "2": "include", "3": "uncertain"}   # pmid 2 updated


def test_previous_screened_pmids(tmp_path):
    (tmp_path / "screening_merged.json").write_text(json.dumps([{"pmid": "1"}, {"pmid": "2"}]))
    assert previous_screened_pmids(tmp_path) == {"1", "2"}
    assert previous_screened_pmids(tmp_path / "missing") == set()
