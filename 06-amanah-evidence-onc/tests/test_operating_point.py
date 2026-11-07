"""Apply the calibrated screening operating point (otto-SR technique #2).

apply_operating_point re-decides records against a validated include-score cutoff;
default (None) is a no-op so existing behavior is unchanged. The cutoff is wired into
both the single-reviewer (screen_batch) and dual-reviewer paths via cfg.
"""
from __future__ import annotations

from core.models import Citation, IdType
from core.sr.config import SRConfig, load_sr_config
from core.sr.models import (
    EnsembleScreeningResult,
    ScreenDecision,
    ScreeningBatch,
    ScreeningResult,
)
from core.sr.screen import apply_operating_point, include_score, screen_batch


def _rec(pmid):
    return Citation(id_type=IdType.PMID, id=pmid, source="pubmed", title="t", snippet="s")


def test_apply_operating_point_none_is_noop():
    rs = [ScreeningResult(pmid="1", decision=ScreenDecision.UNCERTAIN, reason="r", confidence=0.3)]
    assert apply_operating_point(rs, None) is rs   # untouched, same object


def test_apply_operating_point_redecides_and_preserves_votes():
    rs = [
        EnsembleScreeningResult(pmid="hi", decision=ScreenDecision.UNCERTAIN, reason="r",
                                confidence=0.0, ensemble_k=10, include_votes=8),   # score 0.8
        EnsembleScreeningResult(pmid="lo", decision=ScreenDecision.UNCERTAIN, reason="r",
                                confidence=0.0, ensemble_k=10, include_votes=2),   # score 0.2
    ]
    out = {r.pmid: r for r in apply_operating_point(rs, 0.5)}
    assert out["hi"].decision == ScreenDecision.INCLUDE
    assert out["lo"].decision == ScreenDecision.EXCLUDE
    assert out["hi"].include_votes == 8           # ensemble metadata preserved
    assert "calibrated operating point" in out["hi"].reason


def test_confident_exclude_is_admitted_last_not_never():
    # The honest property is MONOTONICITY, not an absolute floor. A confident EXCLUDE
    # scores low (~0.05), so a moderate cutoff keeps it excluded, but an aggressive
    # high-recall cutoff (<= its score) DOES admit it — that is how recall ~1.0 is
    # reachable, and it is never admitted ahead of a higher-scored record.
    rs = [ScreeningResult(pmid="x", decision=ScreenDecision.EXCLUDE, reason="r", confidence=0.95)]
    score = include_score(rs[0])
    assert score < 0.1
    assert apply_operating_point(rs, 0.5)[0].decision == ScreenDecision.EXCLUDE       # moderate cutoff
    assert apply_operating_point(rs, 0.05)[0].decision == ScreenDecision.INCLUDE      # cutoff <= score


def test_excluded_record_keeps_original_exclusion_bucket():
    # PRISMA exclusion accounting buckets by reason text — the cutoff must not erase
    # the eligibility-criterion rationale of an already-excluded record.
    from core.sr.pipeline import _bucket_exclusion
    rs = [ScreeningResult(pmid="r", decision=ScreenDecision.EXCLUDE,
                          reason="Narrative review, secondary literature.", confidence=0.95)]
    out = apply_operating_point(rs, 0.5)[0]
    assert out.decision == ScreenDecision.EXCLUDE
    assert _bucket_exclusion(out.reason) == "review/editorial/no-outcome"   # original bucket kept
    assert "calibrated operating point" in out.reason                        # audit appended


def test_operating_point_admits_in_score_order():
    # Monotonicity: as the cutoff drops, the included set only grows, strictly by score.
    rs = [
        ScreeningResult(pmid="hi", decision=ScreenDecision.INCLUDE, reason="r", confidence=0.9),   # 0.90
        ScreeningResult(pmid="mid", decision=ScreenDecision.UNCERTAIN, reason="r", confidence=0.0),  # 0.50
        ScreeningResult(pmid="lo", decision=ScreenDecision.EXCLUDE, reason="r", confidence=0.95),  # 0.05
    ]
    included = lambda c: {r.pmid for r in apply_operating_point(rs, c)
                          if r.decision == ScreenDecision.INCLUDE}
    s_hi, s_mid, s_lo = (included(0.8), included(0.4), included(0.01))
    assert s_hi == {"hi"}
    assert s_mid == {"hi", "mid"}                  # superset of the stricter cutoff
    assert s_lo == {"hi", "mid", "lo"}
    assert s_hi <= s_mid <= s_lo                   # never drops a record as cutoff lowers


def test_screen_batch_applies_config_cutoff(monkeypatch):
    cfg = load_sr_config()
    # A borderline UNCERTAIN (score 0.5) advances when the cutoff is <= 0.5.
    monkeypatch.setattr(SRConfig, "screening_confidence_cutoff", property(lambda self: 0.4))

    def fake_parse(schema, system, user, **kw):
        return ScreeningBatch(results=[
            ScreeningResult(pmid="1", decision=ScreenDecision.UNCERTAIN, reason="r", confidence=0.0),
        ])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = screen_batch([_rec("1")], cfg)
    assert out[0].decision == ScreenDecision.INCLUDE          # 0.5 >= 0.4
    assert "calibrated operating point" in out[0].reason


def test_config_cutoff_parsing_and_clamping():
    class _Fake:
        pico = {"screening": {"confidence_cutoff": 1.5}}
    prop = SRConfig.screening_confidence_cutoff.fget
    assert prop(_Fake()) == 1.0                                # clamped to [0,1]
    _Fake.pico = {"screening": {"confidence_cutoff": -0.2}}
    assert prop(_Fake()) == 0.0
    _Fake.pico = {"screening": {"confidence_cutoff": "oops"}}
    assert prop(_Fake()) is None
    _Fake.pico = {}
    assert prop(_Fake()) is None                              # default: no cutoff
    # The real default vertical has no cutoff configured.
    assert load_sr_config().screening_confidence_cutoff is None
