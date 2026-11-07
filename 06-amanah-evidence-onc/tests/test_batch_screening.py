"""Message Batches API screening path (otto-SR technique #4: cost/throughput).

The live Anthropic batches SDK is not exercised offline; these tests mock the
batches client to pin the result-shape CONTRACT my code reads, and prove the
fail-closed mapping + the sync fallback. Correctness in production is guaranteed by
the sync path, which the batch path falls back to on any failure.
"""
from __future__ import annotations

from types import SimpleNamespace

import core.llm.client as client
from core.models import Citation, IdType
from core.sr.config import load_sr_config
from core.sr.models import ScreenDecision, ScreeningBatch, ScreeningResult
from core.sr.screen import screen_batch


def _sb(pmid, decision=ScreenDecision.INCLUDE):
    return ScreeningBatch(results=[ScreeningResult(pmid=pmid, decision=decision, reason="r", confidence=0.9)])


def _result(custom_id, parsed=None, *, succeeded=True):
    res = SimpleNamespace(type="succeeded" if succeeded else "errored",
                          message=SimpleNamespace(parsed_output=parsed))
    return SimpleNamespace(custom_id=custom_id, result=res)


class _FakeBatches:
    """Minimal stand-in for client.messages.batches with no polling (ends immediately)."""
    def __init__(self, results):
        self._results = results
        self.created = None

    def create(self, requests):
        self.created = list(requests)
        return SimpleNamespace(id="b1", processing_status="ended")

    def retrieve(self, _bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, _bid):
        return iter(self._results)


def _fake_client(results):
    return SimpleNamespace(messages=SimpleNamespace(batches=_FakeBatches(results)))


def _reqs(n):
    return [(str(i), "sys", f"user {i}") for i in range(n)]


def test_parse_batch_small_n_uses_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "parse",
                        lambda schema, s, u, **kw: calls.append(u) or _sb("X"))
    # Force the batch path to blow up if it is wrongly taken.
    monkeypatch.setattr(client, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("batch used")))
    out = client.parse_batch(ScreeningBatch, _reqs(3), stage="screening")   # 3 < BATCH_MIN(20)
    assert len(calls) == 3
    assert set(out) == {"0", "1", "2"}


def test_parse_batch_maps_ids_via_batches_api(monkeypatch):
    results = [_result(str(i), _sb(f"p{i}")) for i in range(20)]
    monkeypatch.setattr(client, "_get_client", lambda: _fake_client(results))
    out = client.parse_batch(ScreeningBatch, _reqs(20), stage="screening")
    assert set(out) == {str(i) for i in range(20)}
    assert out["5"].results[0].pmid == "p5"


def test_parse_batch_submits_serialized_schema_not_class(monkeypatch):
    # The raw batches.create params must carry the JSON-schema wire form, NOT the
    # pydantic class (which only messages.parse serializes) — else every real run rejects
    # the schema and silently falls back to sync.
    batches = _FakeBatches([_result(str(i), _sb(f"p{i}")) for i in range(20)])
    monkeypatch.setattr(client, "_get_client",
                        lambda: SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    client.parse_batch(ScreeningBatch, _reqs(20), stage="screening")
    params = batches.created[0]["params"]
    # Schema rides under output_config.format (raw batch envelope), not a sibling field.
    fmt = params["output_config"]["format"]
    assert isinstance(fmt, dict)                                  # serialized, not a class
    assert fmt["schema"] == ScreeningBatch.model_json_schema()
    assert "output_format" not in params                         # not the parse()-only field
    assert fmt is not ScreeningBatch


def test_parse_batch_omits_errored_ids(monkeypatch):
    results = [_result("0", _sb("p0"))] + [_result(str(i), None, succeeded=False) for i in range(1, 20)]
    monkeypatch.setattr(client, "_get_client", lambda: _fake_client(results))
    out = client.parse_batch(ScreeningBatch, _reqs(20), stage="screening")
    assert set(out) == {"0"}          # errored ids fail closed (omitted)


def test_parse_batch_falls_back_to_sync_on_failure(monkeypatch):
    def boom():
        raise RuntimeError("batches API unavailable")
    monkeypatch.setattr(client, "_get_client", boom)
    monkeypatch.setattr(client, "parse", lambda schema, s, u, **kw: _sb("fb"))
    out = client.parse_batch(ScreeningBatch, _reqs(25), stage="screening")
    assert len(out) == 25             # every request still resolved via sync fallback


def _rec(pmid):
    return Citation(id_type=IdType.PMID, id=pmid, source="pubmed", title="t", snippet="s")


def test_screen_batch_use_batch_guards_and_recovers(monkeypatch):
    cfg = load_sr_config()
    recs = [_rec("111"), _rec("222")]   # one chunk (batch_size default)

    def fake_parse_batch(schema, requests, **kw):
        # Return a guarded-able batch for chunk "0": includes a hallucinated id + drops 222.
        return {"0": ScreeningBatch(results=[
            ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE, reason="ok", confidence=0.9),
            ScreeningResult(pmid="999", decision=ScreenDecision.INCLUDE, reason="hallucinated", confidence=0.9),
        ])}

    monkeypatch.setattr(client, "parse_batch", fake_parse_batch)
    out = {r.pmid: r for r in screen_batch(recs, cfg, use_batch=True)}
    assert set(out) == {"111", "222"}                       # 999 dropped, 222 recovered
    assert out["222"].decision == ScreenDecision.UNCERTAIN  # guarded, never lost


def test_screen_batch_dropped_chunk_falls_back_per_record(monkeypatch):
    cfg = load_sr_config()
    recs = [_rec("111")]
    monkeypatch.setattr(client, "parse_batch", lambda schema, requests, **kw: {})  # batch returned nothing
    monkeypatch.setattr(client, "parse",
                        lambda schema, s, u, **kw: ScreeningResult(
                            pmid="111", decision=ScreenDecision.INCLUDE, reason="sync", confidence=0.9))
    out = screen_batch(recs, cfg, use_batch=True)
    assert out[0].pmid == "111"
    assert out[0].decision == ScreenDecision.INCLUDE        # per-record sync fallback kept it
