"""Harness backend contracts: pause, resume, guardrails, and provenance."""

from __future__ import annotations

import json

import pytest

from core.llm import client
from core.models import Citation, IdType, RetrievalResult
from core.render import provenance as provenance_store
from core.sr.agent_io import dump_corpus, records_for_agents
from core.sr.pipeline import Corpus
from core.tools import registry
from core.tools.harness import HarnessWorkPending
from core.tools.runner import run_tool


def _corpus(tmp_path, n: int = 2) -> Corpus:
    from core.sr.config import load_sr_config

    cfg = load_sr_config("mm-bcma-3l")
    citations = [
        Citation(
            id_type=IdType.PMID,
            id=str(100 + i),
            source="pubmed",
            title=f"Trial {i}",
            snippet=f"BCMA study abstract {i}.",
            verified=True,
            verification="live",
        )
        for i in range(n)
    ]
    return Corpus(
        cfg=cfg,
        run_id=f"harness-{tmp_path.name}",
        search_result=RetrievalResult(source="pubmed", query="q", citations=citations),
        identified=citations,
        deduped=citations,
        verified=citations,
        rejected=[],
        seed_verification={},
        per_source={"pubmed": len(citations)},
        sources_executed=["pubmed"],
    )


def _write_corpus(run_dir, n: int = 2) -> list[dict]:
    corpus = _corpus(run_dir, n)
    dump_corpus(corpus, run_dir / "corpus.json")
    records = records_for_agents(corpus)
    (run_dir / "records_for_agents.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return records


def _screen_row(pmid: str, decision: str = "include") -> dict:
    return {
        "pmid": pmid,
        "decision": decision,
        "reason": "eligible" if decision == "include" else "not eligible",
        "confidence": 0.9,
        "modality": "CAR-T",
        "study_design": "single-arm",
    }


def test_harness_screen_writes_distinct_requests_and_pauses(tmp_path):
    registry.load_builtins()
    _write_corpus(tmp_path)

    for source in ("claude", "gpt"):
        with pytest.raises(HarnessWorkPending, match="AWAITING AGENT"):
            run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": source},
                     override="harness")
        request = tmp_path / "harness" / "screen" / source / "REQUEST.md"
        assert request.exists()
        assert f"Source: " in request.read_text(encoding="utf-8")
        assert (tmp_path / "harness" / "screen" / source / "screening_in" / "batch_000.json").exists()

    assert not (tmp_path / "screening_merged.json").exists()


def test_harness_partial_result_file_stays_pending(tmp_path):
    registry.load_builtins()
    records = _write_corpus(tmp_path, n=31)

    with pytest.raises(HarnessWorkPending):
        run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
                 override="harness")

    hdir = tmp_path / "harness" / "screen" / "gpt"
    (hdir / "screen_batch_000.json").write_text(
        json.dumps([_screen_row(r["pmid"], "exclude") for r in records[:30]]),
        encoding="utf-8")
    with pytest.raises(HarnessWorkPending):
        run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
                 override="harness")
    assert not (tmp_path / "screening_merged.json").exists()


def test_harness_screen_collect_matches_api_guardrails(tmp_path):
    registry.load_builtins()
    records = _write_corpus(tmp_path, n=2)
    expected_pmids = [r["pmid"] for r in records]

    with pytest.raises(HarnessWorkPending):
        run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
                 override="harness")

    hdir = tmp_path / "harness" / "screen" / "gpt"
    (hdir / "screen_batch_000.json").write_text(
        json.dumps([_screen_row(expected_pmids[0], "include"), _screen_row("999", "include")]),
        encoding="utf-8")

    out = run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
                   override="harness")
    rows = json.loads(out.read_text(encoding="utf-8"))
    by_pmid = {row["pmid"]: row for row in rows}
    assert set(by_pmid) == set(expected_pmids)
    assert by_pmid[expected_pmids[0]]["decision"] == "include"
    assert by_pmid[expected_pmids[1]]["decision"] == "uncertain"
    assert json.loads((tmp_path / "includes.json").read_text(encoding="utf-8")) == [expected_pmids[0]]


def test_harness_extract_resume_writes_normal_artifacts(tmp_path):
    registry.load_builtins()
    records = _write_corpus(tmp_path, n=1)
    pmid = records[0]["pmid"]
    (tmp_path / "screening_merged.json").write_text(
        json.dumps([_screen_row(pmid, "include")]), encoding="utf-8")
    (tmp_path / "includes.json").write_text(json.dumps([pmid]), encoding="utf-8")

    with pytest.raises(HarnessWorkPending):
        run_tool("extract", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "claude"},
                 override="harness")

    hdir = tmp_path / "harness" / "extract" / "claude"
    (hdir / "extract_idx_000.json").write_text(json.dumps({
        "pmid": pmid,
        "study_name": "Trial 0",
        "modality": "CAR-T",
        "values": [
            {"field": "complete_response_rate", "group": "efficacy",
             "value": "80%", "quote": "CR 80%"},
            {"field": "total_n", "group": "population", "value": "100", "quote": "100 patients"},
        ],
    }), encoding="utf-8")

    out = run_tool("extract", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "claude"},
                   override="harness")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [e["pmid"] for e in data["extractions"]] == [pmid]
    assert (tmp_path / "extract_idx_000.json").exists()


def test_harness_provenance_resume_is_idempotent(tmp_path):
    from core.tools.sr_io import load_run_bundle

    registry.load_builtins()
    client.reset_stage_log()
    records = _write_corpus(tmp_path, n=2)
    expected_pmids = [r["pmid"] for r in records]

    with pytest.raises(HarnessWorkPending):
        run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
                 override="harness")
    hdir = tmp_path / "harness" / "screen" / "gpt"
    (hdir / "screen_batch_000.json").write_text(
        json.dumps([_screen_row(expected_pmids[0], "include"), _screen_row(expected_pmids[1], "exclude")]),
        encoding="utf-8")

    run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
             override="harness")
    run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
             override="harness")
    (tmp_path / "extractions.json").write_text(json.dumps({"extractions": []}), encoding="utf-8")

    client.reset_stage_log()
    bundle = load_run_bundle(tmp_path, "mm-bcma-3l")
    prov = provenance_store.read(bundle.run.provenance_id)
    assert prov is not None
    entries = [e for e in prov.stage_models if e.get("stage") == "screening"
               and e.get("backend") == "harness"]
    assert entries == [{
        "stage": "screening",
        "model": "GPT (ChatGPT Pro / Codex)",
        "backend": "harness",
        "n_calls": 1,
    }]

    bundle = load_run_bundle(tmp_path, "mm-bcma-3l")
    prov = provenance_store.read(bundle.run.provenance_id)
    entries = [e for e in prov.stage_models if e.get("stage") == "screening"
               and e.get("backend") == "harness"]
    assert len(entries) == 1


def test_recipe_stops_cleanly_at_pending_harness_step(monkeypatch, tmp_path):
    from core.tools import recipes

    registry.load_builtins()

    async def fake_gather(cfg, retmax=None):
        return _corpus(tmp_path, n=1)

    monkeypatch.setattr("core.sr.pipeline.gather_corpus", fake_gather)

    with pytest.raises(HarnessWorkPending, match="recipe 'quick-look' paused at step 'screen'"):
        recipes.run_recipe(
            "quick-look", tmp_path,
            {"vertical": "mm-bcma-3l", "harness_source": "gpt"},
            override="harness",
        )
    assert (tmp_path / "harness" / "screen" / "gpt" / "REQUEST.md").exists()
    assert not (tmp_path / "screening_merged.json").exists()


def test_cli_harness_pause_prints_clean_awaiting_agent(capsys, tmp_path):
    from core.tools.cli import main

    _write_corpus(tmp_path, n=1)
    main([
        "run", "screen", str(tmp_path), "--vertical", "mm-bcma-3l",
        "--harness", "--harness-source", "gpt",
    ])
    captured = capsys.readouterr()
    assert "AWAITING AGENT" in captured.out
    assert captured.err == ""
