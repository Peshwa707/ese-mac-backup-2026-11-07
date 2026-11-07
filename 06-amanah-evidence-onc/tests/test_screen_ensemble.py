"""Self-consistency ensemble screening (replicate-and-improve of otto-SR/srma).

aggregate_ensemble is a pure function (no network) so the voting/tie logic is
tested directly; screen_batch is exercised with a mocked LLM to prove k=1 is the
unchanged single-pass path and k>1 runs k passes and aggregates the votes.
"""
from __future__ import annotations

from core.models import Citation, IdType
from core.sr.config import load_sr_config
from core.sr.models import (
    EnsembleScreeningResult,
    ScreenDecision,
    ScreeningBatch,
    ScreeningResult,
)
from core.sr.screen import aggregate_ensemble, screen_batch


def _rec(pmid: str) -> Citation:
    return Citation(id_type=IdType.PMID, id=pmid, source="pubmed", title=f"t{pmid}", snippet=f"s{pmid}")


def _res(pmid: str, decision: ScreenDecision, conf: float = 0.9) -> ScreeningResult:
    return ScreeningResult(pmid=pmid, decision=decision, reason="r", confidence=conf)


def test_aggregate_majority_wins_and_records_votes():
    recs = [_rec("111")]
    samples = [
        [_res("111", ScreenDecision.INCLUDE, 0.8)],
        [_res("111", ScreenDecision.INCLUDE, 0.6)],
        [_res("111", ScreenDecision.EXCLUDE, 0.9)],
    ]
    out = {r.pmid: r for r in aggregate_ensemble(recs, samples)}
    r = out["111"]
    assert r.decision == ScreenDecision.INCLUDE
    assert r.include_votes == 2
    assert r.ensemble_k == 3
    # confidence = mean over the samples matching the final decision (0.8, 0.6)
    assert abs(r.confidence - 0.7) < 1e-9


def test_aggregate_hard_split_defers_to_human():
    recs = [_rec("222")]
    samples = [
        [_res("222", ScreenDecision.INCLUDE)],
        [_res("222", ScreenDecision.EXCLUDE)],
    ]
    r = aggregate_ensemble(recs, samples)[0]
    assert r.decision == ScreenDecision.UNCERTAIN   # 1-1 tie is never guessed
    assert r.include_votes == 1


def test_aggregate_keeps_uncertain_as_plurality():
    recs = [_rec("333")]
    samples = [
        [_res("333", ScreenDecision.UNCERTAIN)],
        [_res("333", ScreenDecision.UNCERTAIN)],
        [_res("333", ScreenDecision.INCLUDE)],
    ]
    r = aggregate_ensemble(recs, samples)[0]
    assert r.decision == ScreenDecision.UNCERTAIN
    assert r.include_votes == 1


def test_aggregate_dedups_duplicate_pmid_within_a_pass():
    # A single pass that echoes the same PMID twice must cast only ONE vote, so
    # include_votes can never exceed ensemble_k (k=2 here).
    recs = [_rec("111")]
    samples = [
        [_res("111", ScreenDecision.INCLUDE), _res("111", ScreenDecision.INCLUDE)],  # dup in one pass
        [_res("111", ScreenDecision.EXCLUDE)],
    ]
    r = aggregate_ensemble(recs, samples)[0]
    assert r.ensemble_k == 2
    assert r.include_votes == 1          # the duplicate did not double-count
    assert r.decision == ScreenDecision.UNCERTAIN   # 1 include vs 1 exclude -> tie


def test_guarded_results_keeps_one_decision_per_record():
    from core.sr.screen import _guarded_results
    recs = [_rec("111")]
    out = _guarded_results(recs, [
        _res("111", ScreenDecision.INCLUDE, 0.9),
        _res("111", ScreenDecision.EXCLUDE, 0.1),   # duplicate echo
        _res("999", ScreenDecision.INCLUDE),        # hallucinated
    ])
    assert len(out) == 1
    assert out[0].pmid == "111"
    assert out[0].decision == ScreenDecision.INCLUDE   # first decision wins


def test_screen_batch_k1_is_unchanged_single_pass(monkeypatch):
    cfg = load_sr_config()
    recs = [_rec("111"), _rec("222")]
    calls = []

    def fake_parse(schema, system, user, **kw):
        calls.append(user)
        return ScreeningBatch(results=[
            _res("111", ScreenDecision.INCLUDE, 0.91),
            _res("222", ScreenDecision.EXCLUDE, 0.77),
        ])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = {r.pmid: r for r in screen_batch(recs, cfg)}   # default => cfg k (1)
    assert len(calls) == 1                                # one pass, one chunk
    r = out["111"]
    assert type(r) is ScreeningResult                     # plain record, not ensemble
    assert r.decision == ScreenDecision.INCLUDE
    assert r.confidence == 0.91                           # untouched
    # F1 regression: k=1 serialization must NOT gain ensemble keys.
    assert "ensemble_k" not in r.model_dump()
    assert "include_votes" not in r.model_dump()


def test_screening_result_schema_excludes_ensemble_fields():
    # F1 regression: ScreeningResult is the LLM output schema — the model must never
    # be asked to fill ensemble metadata.
    props = ScreeningResult.model_json_schema()["properties"]
    assert "ensemble_k" not in props
    assert "include_votes" not in props


def test_ensemble_result_serializes_votes():
    # k>1 aggregated records DO carry votes (so calibration can read them later).
    r = aggregate_ensemble([_rec("111")], [
        [_res("111", ScreenDecision.INCLUDE)],
        [_res("111", ScreenDecision.INCLUDE)],
    ])[0]
    assert isinstance(r, EnsembleScreeningResult)
    dumped = r.model_dump()
    assert dumped["include_votes"] == 2
    assert dumped["ensemble_k"] == 2


def test_screen_batch_honors_config_ensemble_k(monkeypatch):
    # F2 regression: with no explicit arg, screen_batch must read the vertical's
    # screening.ensemble_k so every single-reviewer caller honors it.
    from core.sr.config import SRConfig
    cfg = load_sr_config()
    monkeypatch.setattr(SRConfig, "screening_ensemble_k", property(lambda self: 3))
    n = {"calls": 0}

    def fake_parse(schema, system, user, **kw):
        n["calls"] += 1
        return ScreeningBatch(results=[_res("111", ScreenDecision.INCLUDE, 0.9)])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = screen_batch([_rec("111")], cfg)   # no ensemble_k passed
    assert n["calls"] == 3                    # config-driven, not bypassed
    assert isinstance(out[0], EnsembleScreeningResult)
    assert out[0].include_votes == 3


def test_screen_batch_ensemble_runs_k_passes_and_votes(monkeypatch):
    cfg = load_sr_config()
    recs = [_rec("111")]
    n = {"calls": 0}

    def fake_parse(schema, system, user, **kw):
        # Two passes say INCLUDE, the third says EXCLUDE -> majority INCLUDE, 2 votes.
        n["calls"] += 1
        decision = ScreenDecision.EXCLUDE if n["calls"] == 3 else ScreenDecision.INCLUDE
        return ScreeningBatch(results=[_res("111", decision, 0.9)])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = screen_batch(recs, cfg, ensemble_k=3)[0]
    assert n["calls"] == 3                                # k independent passes
    assert out.decision == ScreenDecision.INCLUDE
    assert out.include_votes == 2
    assert out.ensemble_k == 3
