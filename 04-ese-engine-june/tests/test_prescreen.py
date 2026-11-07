"""WP3 — deterministic publication-type pre-screen filter.

Editorials/letters/comments are excluded by type (protocol 2.2.3); conference
abstracts are routed to an abstract-only sensitivity bucket (protocol 2.2.4);
trials and articles always reach normal screening. Type names follow PubMed
PublicationType and Embase M3 values.
"""
from core.sr.prescreen import classify_by_type, PRESCREEN_BUCKETS


def test_editorial_letter_comment_excluded_by_type():
    recs = [
        {"pmid": "1", "study_type": "Editorial"},
        {"pmid": "2", "study_type": "Letter"},
        {"pmid": "3", "study_type": "Comment"},
        {"pmid": "4", "study_type": "Published Erratum"},
        {"pmid": "5", "study_type": "Review"},
    ]
    out = classify_by_type(recs)
    assert all(out[p]["bucket"] == "exclude_by_type" for p in ("1", "2", "3", "4", "5"))


def test_conference_abstract_routed_to_sensitivity():
    out = classify_by_type([{"pmid": "2", "study_type": "Conference Abstract"},
                            {"pmid": "9", "study_type": "Conference Paper"}])
    assert out["2"]["bucket"] == "conference_abstract"
    assert out["9"]["bucket"] == "conference_abstract"


def test_trials_and_articles_pass_to_screening():
    recs = [
        {"pmid": "3", "study_type": "Randomized Controlled Trial"},
        {"pmid": "6", "study_type": "Clinical Trial"},
        {"pmid": "7", "study_type": "Journal Article"},
        {"pmid": "8", "study_type": "Article"},
        {"pmid": "10", "study_type": ""},   # unknown/blank -> screen (never silently drop)
    ]
    out = classify_by_type(recs)
    assert all(out[p]["bucket"] == "screen" for p in ("3", "6", "7", "8", "10"))


def test_trial_type_overrides_exclude():
    # a record tagged both "Letter" and "Clinical Trial" must NOT be excluded by type
    out = classify_by_type([{"pmid": "11", "study_type": "Letter; Clinical Trial"}])
    assert out["11"]["bucket"] == "screen"


def test_conference_takes_precedence_over_trial():
    # a conference abstract OF a trial is still abstract-only data -> sensitivity bucket
    out = classify_by_type([{"pmid": "12", "study_type": "Conference Abstract; Clinical Trial"}])
    assert out["12"]["bucket"] == "conference_abstract"


def test_buckets_constant_exposed():
    assert set(PRESCREEN_BUCKETS) == {"exclude_by_type", "conference_abstract", "screen"}


def test_prescreen_records_partitions_and_reports():
    from core.sr.prescreen import prescreen_records
    recs = [
        {"pmid": "1", "study_type": "Editorial"},
        {"pmid": "2", "study_type": "Conference Abstract"},
        {"pmid": "3", "study_type": "Randomized Controlled Trial"},
        {"pmid": "4", "study_type": "Article"},
    ]
    to_screen, report = prescreen_records(recs)
    assert {r["pmid"] for r in to_screen} == {"3", "4"}        # editorial + conf removed
    assert report["exclude_by_type"] == ["1"]
    assert report["conference_abstract"] == ["2"]
    assert report["counts"] == {"screen": 2, "exclude_by_type": 1, "conference_abstract": 1}


def test_run_screening_applies_prescreen_before_llm(monkeypatch):
    """Integration: the native screening entry must NOT pass editorials to the LLM."""
    import core.sr.screen as screen_mod
    seen = {}
    def fake_screen_batch(citations, cfg):
        seen["pmids"] = [c.id for c in citations]
        return []
    monkeypatch.setattr(screen_mod, "screen_batch", fake_screen_batch)
    from core.sr.agents.api_executor import run_screening
    run_screening([
        {"pmid": "100", "title": "An editorial", "study_type": "Editorial", "abstract": ""},
        {"pmid": "200", "title": "A phase 3 RCT", "study_type": "Randomized Controlled Trial", "abstract": "x"},
    ], cfg=None)
    assert "100" not in seen["pmids"]      # editorial filtered out before screening
    assert "200" in seen["pmids"]          # trial reaches screening


def test_load_prescreen_report_absent_returns_empty(tmp_path):
    from core.sr.prescreen import load_prescreen_report
    assert load_prescreen_report(tmp_path) == {}   # no prescreen.json -> legitimately empty


def test_load_prescreen_report_corrupt_fails_closed(tmp_path):
    import pytest
    from core.sr.prescreen import load_prescreen_report
    (tmp_path / "prescreen.json").write_text("{ not valid json")
    with pytest.raises(Exception):                 # corrupt artifact must NOT silently -> {}
        load_prescreen_report(tmp_path)
    (tmp_path / "prescreen.json").write_text('{"no_counts": true}')
    with pytest.raises(ValueError):                # present but missing 'counts' -> fail closed
        load_prescreen_report(tmp_path)
    (tmp_path / "prescreen.json").write_text('{"counts": {}}')
    with pytest.raises(ValueError):                # counts present but empty (no 'screen') -> fail closed
        load_prescreen_report(tmp_path)
    (tmp_path / "prescreen.json").write_text('{"counts": {"exclude_by_type": 1}}')
    with pytest.raises(ValueError):                # partial counts (missing 'screen') -> fail closed
        load_prescreen_report(tmp_path)
    (tmp_path / "prescreen.json").write_text(
        '{"counts": {"screen": true, "exclude_by_type": 1, "conference_abstract": 2}}')
    with pytest.raises(ValueError):                # bool is not a valid count -> fail closed
        load_prescreen_report(tmp_path)
    # a complete, well-formed report loads fine
    (tmp_path / "prescreen.json").write_text(
        '{"counts": {"screen": 5, "exclude_by_type": 1, "conference_abstract": 2}}')
    assert load_prescreen_report(tmp_path)["counts"]["screen"] == 5
