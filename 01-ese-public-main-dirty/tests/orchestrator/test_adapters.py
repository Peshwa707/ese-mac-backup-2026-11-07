"""WP-M1.4a — adapter framework + deterministic stats/grade adapters."""
import json
from dataclasses import dataclass

import pytest

from core.sr.orchestrator import adapters as ad
from core.sr.orchestrator.stages import RunContext, StageResult
from core.sr.models import ExtractedStudy, ExtractedValue, ScreeningResult, ScreenDecision


# ---- framework ----
@dataclass
class _DC:
    a: int
    b: str


def test_to_jsonable_dataclass_and_pydantic():
    assert ad.to_jsonable(_DC(1, "x")) == {"a": 1, "b": "x"}
    sr = ScreeningResult(pmid="1", decision=ScreenDecision.INCLUDE, reason="r", confidence=0.9)
    assert ad.to_jsonable(sr)["pmid"] == "1"
    assert ad.to_jsonable([_DC(1, "x")]) == [{"a": 1, "b": "x"}]


def test_write_json_returns_path_and_stable_hash(tmp_path):
    p1, h1 = ad.write_json(tmp_path, "a.json", {"k": "v"})
    assert p1.exists() and len(h1) == 64
    _, h2 = ad.write_json(tmp_path, "a.json", {"k": "v"})
    assert h1 == h2                                   # deterministic
    _, h3 = ad.write_json(tmp_path, "a.json", {"k": "w"})
    assert h3 != h1


def test_write_json_hash_is_key_order_invariant(tmp_path):
    _, h1 = ad.write_json(tmp_path, "x.json", {"a": 1, "b": 2})
    _, h2 = ad.write_json(tmp_path, "x.json", {"b": 2, "a": 1})
    assert h1 == h2                                   # canonical (sort_keys) hashing


def test_run_async_refuses_nested_loop():
    async def _inner():
        return ad.run_async(_coro())

    async def _coro():
        return 1

    with pytest.raises(RuntimeError, match="running event loop"):
        import asyncio
        asyncio.run(_inner())


# ---- fixtures ----
def _study(pmid, orr="80% (40/50)", hr="0.50 (0.30-0.80)", comparator="PVd"):
    vals = [
        ExtractedValue(field="orr", group="efficacy", value=orr, flag="", quote="q"),
        ExtractedValue(field="pfs_hr_vs_comparator", group="efficacy", value=hr, flag="", quote="q"),
        ExtractedValue(field="comparator_label", group="population", value=comparator, flag="", quote="q"),
    ]
    return ExtractedStudy(pmid=pmid, study_name=f"T{pmid}", modality="CAR-T",
                          data_cut="2026", values=vals)


def _cfg():
    from core.sr.config import load_sr_config
    return load_sr_config("mm-bcma-early-relapse")


def _write_extractions(run_dir, studies):
    (run_dir / "extractions.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in studies]), encoding="utf-8")


# ---- stats adapter ----
def test_stats_adapter_loads_runs_writes_and_hashes(tmp_path):
    _write_extractions(tmp_path, [_study("1"), _study("2")])
    res = ad.stats_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    assert isinstance(res, StageResult)
    assert (tmp_path / "stats.json").exists()
    assert "stats.json" in res.outputs
    for k in ("available", "n_proportions", "n_hazard_ratios", "note"):
        assert k in res.summary
    assert "extractions.json" in res.deterministic_inputs   # input hash recorded
    json.loads((tmp_path / "stats.json").read_text())       # parseable artifact


# ---- grade adapter ----
def test_grade_adapter_writes_grade_artifact(tmp_path):
    _write_extractions(tmp_path, [_study("1"), _study("2")])
    screening = [ScreeningResult(pmid=p, decision=ScreenDecision.INCLUDE, reason="ok",
                                 confidence=0.9, study_design="rct") for p in ("1", "2")]
    (tmp_path / "screening.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in screening]), encoding="utf-8")
    res = ad.grade_adapter(tmp_path, RunContext(cfg=_cfg(), run_dir=tmp_path))
    assert (tmp_path / "grade.json").exists()
    assert "n_outcomes_graded" in res.summary
    json.loads((tmp_path / "grade.json").read_text())
    # consumed-artifact hashes must be recorded so stale screening/extractions
    # can't leave grade.json wrongly treated as current.
    assert set(res.deterministic_inputs) == {"extractions.json", "screening.json"}
    assert all(len(h) == 64 for h in res.deterministic_inputs.values())
