"""WP4 — named-trial sentinel (positive control for screening)."""
from core.sr.sentinel import check_named_trials


def test_sentinel_fails_when_named_trial_excluded():
    # MajesTEC-9's article TITLE has no "MajesTEC-9"; the abstract does -> must still match.
    screened = [{
        "pmid": "42212933",
        "title": "Teclistamab in Multiple Myeloma with One to Three Previous Lines of Therapy",
        "abstract": "...patients in the MajesTEC-9 trial were randomly assigned...",
        "decision": "exclude",
    }]
    report = check_named_trials(["MajesTEC-9"], screened, min_stage="advance")
    assert report["passed"] is False
    assert "MajesTEC-9" in report["violations"]
    assert report["matched"]["MajesTEC-9"].startswith("matched-but-")


def test_sentinel_passes_when_advanced():
    screened = [{
        "pmid": "42212933",
        "title": "Teclistamab in Multiple Myeloma with One to Three Previous Lines",
        "abstract": "the MajesTEC-9 trial",
        "decision": "include",
    }]
    rep = check_named_trials(["MajesTEC-9"], screened, min_stage="advance")
    assert rep["passed"] is True and rep["matched"]["MajesTEC-9"] in ("advanced", "include")


def test_uncertain_counts_as_advanced():
    screened = [{"pmid": "1", "title": "CARTITUDE-4 cilta-cel", "decision": "uncertain"}]
    assert check_named_trials(["CARTITUDE-4"], screened)["passed"] is True


def test_not_found_is_a_violation():
    rep = check_named_trials(["MajesTEC-9"], [{"pmid": "9", "title": "unrelated", "decision": "include"}])
    assert rep["passed"] is False
    assert rep["matched"]["MajesTEC-9"] == "not-found"


def test_hyphen_and_case_insensitive_match():
    screened = [{"pmid": "1", "title": "Results of the majestec 9 study", "decision": "include"}]
    assert check_named_trials(["MajesTEC-9"], screened)["passed"] is True


def test_numbered_variant_does_not_satisfy_prefix_trial():
    # A KarMMa-3 record must NOT satisfy a configured plain "KarMMa" sentinel
    # (prefix-collision bug: "karmma" is a substring of "karmma-3").
    screened = [{"pmid": "1", "title": "KarMMa-3: idecabtagene vicleucel", "decision": "include"}]
    rep = check_named_trials(["KarMMa", "KarMMa-3"], screened)
    assert "KarMMa" in rep["violations"]          # plain KarMMa NOT satisfied
    assert "KarMMa-3" not in rep["violations"]     # KarMMa-3 correctly matched
    assert rep["passed"] is False


def test_plain_trial_still_matches_its_own_record():
    screened = [{"pmid": "1", "title": "The KarMMa trial of ide-cel", "decision": "include"}]
    assert check_named_trials(["KarMMa"], screened)["passed"] is True


def test_hyphen_prefixed_variant_does_not_satisfy_base_trial():
    # A J-ALEX record must NOT satisfy a configured plain "ALEX" sentinel.
    screened = [{"pmid": "1", "title": "J-ALEX: alectinib vs crizotinib", "decision": "include"}]
    rep = check_named_trials(["ALEX", "J-ALEX"], screened)
    assert "ALEX" in rep["violations"]            # base ALEX NOT satisfied by J-ALEX
    assert "J-ALEX" not in rep["violations"]       # J-ALEX correctly matched
    # but a genuine ALEX record still matches:
    assert check_named_trials(["ALEX"], [{"pmid": "2", "title": "the ALEX study", "decision": "include"}])["passed"]


def test_spaced_numbered_variant_does_not_satisfy_prefix_trial():
    # "KarMMa - 3" (spaced hyphen) must not satisfy a plain "KarMMa" sentinel
    for title in ["KarMMa - 3: idecabtagene vicleucel", "KarMMa  3 study", "KarMMa-3 study"]:
        rep = check_named_trials(["KarMMa"], [{"pmid": "1", "title": title, "decision": "include"}])
        assert rep["passed"] is False, title


# --- build_run integration: sentinel report + strict-mode raise ---

def _corpus_with_excluded_named_trial():
    from core.models import Citation, IdType, RetrievalResult
    from core.sr.config import load_sr_config
    from core.sr.pipeline import Corpus
    cfg = load_sr_config("mm-bcma-3l")   # named_trials include CARTITUDE-1
    cites = [Citation(id_type=IdType.PMID, id="c1", source="pubmed",
                      title="CARTITUDE-1: ciltacabtagene autoleucel in myeloma",
                      snippet="phase 1b/2 of cilta-cel", verified=True, verification="live")]
    corpus = Corpus(cfg=cfg, run_id="t", search_result=RetrievalResult(source="pubmed", query="q", citations=cites),
                    identified=cites, deduped=cites, verified=cites, rejected=[], seed_verification={},
                    per_source={"pubmed": 1}, sources_executed=["pubmed"])
    return cfg, corpus


def test_build_run_records_sentinel_violation_warn_mode():
    from core.sr.models import ScreeningResult, ScreenDecision
    from core.sr.pipeline import build_run
    cfg, corpus = _corpus_with_excluded_named_trial()
    screening = [ScreeningResult(pmid="c1", decision=ScreenDecision.EXCLUDE,
                                 reason="x", confidence=0.9)]
    run = build_run(corpus, screening, [])      # default: warn, do not raise
    assert run.sentinel["passed"] is False
    assert "CARTITUDE-1" in run.sentinel["violations"]


def test_build_run_sentinel_strict_raises(monkeypatch):
    import pytest
    from core.sr.config import SRConfig
    from core.sr.models import ScreeningResult, ScreenDecision
    from core.sr.pipeline import build_run
    from core.sr.sentinel import SentinelViolation
    cfg, corpus = _corpus_with_excluded_named_trial()
    # Flip strict via monkeypatch on the class property — auto-reverts, never mutates
    # the lru-cached config (avoids leaking strict mode into other tests).
    monkeypatch.setattr(SRConfig, "sentinel_strict", property(lambda self: True))
    screening = [ScreeningResult(pmid="c1", decision=ScreenDecision.EXCLUDE,
                                 reason="x", confidence=0.9)]
    with pytest.raises(SentinelViolation):
        build_run(corpus, screening, [])
