"""Contract tests for the built-in tools + the CLI surface (no live deps)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.models import Citation, IdType, RetrievalResult
from core.sr.models import (
    ExtractedStudy,
    ExtractedValue,
    RoBAssessment,
    RoBDomain,
    ScreenDecision,
    ScreeningBatch,
    ScreeningResult,
)
from core.sr.pipeline import Corpus
from core.tools import registry
from core.tools.posture import resolve_backend


def _load():
    registry.load_builtins()
    return {t.name: t for t in registry.all_tools()}


def test_builtins_registered_with_sane_contracts():
    tools = _load()
    assert {"search", "screen", "extract", "pool-proportions", "metabin", "metacont",
            "meta-regression", "fulltext", "risk-of-bias", "grade", "narrative",
            "manuscript", "pool-configured", "quick-look-summary"} <= set(tools)
    # The artifact graph chains: search writes what screen reads.
    assert tools["search"].output_artifact == "corpus.json"
    assert "corpus.json" in tools["screen"].input_artifacts
    assert "screening_merged.json" in tools["extract"].input_artifacts
    assert tools["metabin"].output_artifact == "stats_binary.json"
    assert tools["metacont"].output_artifact == "stats_continuous.json"
    assert tools["meta-regression"].output_artifact == "stats_metareg.json"
    assert tools["fulltext"].output_artifact == "fulltext_report.json"
    assert tools["risk-of-bias"].output_artifact == "rob.json"
    assert tools["grade"].conclusion_bearing is True
    assert tools["narrative"].conclusion_bearing is True
    assert tools["manuscript"].conclusion_bearing is True
    assert tools["quick-look-summary"].conclusion_bearing is False


def test_backend_policies_match_design():
    tools = _load()
    # search + pool-proportions are math -> no brain.
    assert resolve_backend(tools["search"]) == "n/a"
    assert resolve_backend(tools["pool-proportions"]) == "n/a"
    # screen is an AI tool, cloud by default today (posture=cloud).
    assert tools["screen"].kind == "ai"
    assert resolve_backend(tools["screen"], posture="cloud") == "cloud"
    assert resolve_backend(tools["screen"], posture="harness") == "harness"
    assert tools["screen"].backend.harness_enabled is True
    assert tools["extract"].backend.harness_enabled is True
    assert tools["narrative"].backend.harness_enabled is False
    # pool-proportions has passed an external benchmark; screen has not yet.
    assert tools["pool-proportions"].scorecard.externally_validated is True
    assert tools["screen"].scorecard.externally_validated is False
    assert tools["extract"].scorecard.externally_validated is False
    assert tools["metabin"].scorecard.externally_validated is False
    assert tools["metacont"].scorecard.externally_validated is False
    assert tools["meta-regression"].scorecard.externally_validated is False
    assert tools["risk-of-bias"].scorecard.externally_validated is False
    assert tools["grade"].scorecard.externally_validated is False
    assert tools["narrative"].scorecard.externally_validated is False
    assert tools["manuscript"].scorecard.externally_validated is False


def _corpus(run_id="run1"):
    from core.sr.config import load_sr_config

    cfg = load_sr_config("mm-bcma-3l")
    citations = [
        Citation(id_type=IdType.PMID, id="111", source="pubmed", title="Trial 1",
                 snippet="BCMA CAR-T trial.", verified=True, verification="live"),
        Citation(id_type=IdType.PMID, id="222", source="pubmed", title="Trial 2",
                 snippet="Bispecific antibody trial.", verified=True, verification="live"),
    ]
    return Corpus(
        cfg=cfg,
        run_id=run_id,
        search_result=RetrievalResult(source="pubmed", query="q", citations=citations),
        identified=citations,
        deduped=citations,
        verified=citations,
        rejected=[],
        seed_verification={},
        per_source={"pubmed": len(citations)},
        sources_executed=["pubmed"],
    )


def test_search_writes_canonical_run_folder_files(monkeypatch, tmp_path):
    async def fake_gather(cfg, retmax=None):
        return _corpus()

    import core.sr.pipeline as sr_pipeline
    monkeypatch.setattr(sr_pipeline, "gather_corpus", fake_gather)

    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("search", tmp_path, {"vertical": "mm-bcma-3l", "retmax": 2})

    assert out == tmp_path / "corpus.json"
    corpus = json.loads(out.read_text())
    records = json.loads((tmp_path / "records_for_agents.json").read_text())
    assert corpus["run_id"] == "run1"
    assert set(corpus) >= {"search_result", "identified", "deduped", "verified", "rejected"}
    assert [r["pmid"] for r in records] == ["111", "222"]


def test_screen_uses_batch_guardrails_and_writes_includes(monkeypatch, tmp_path):
    from core.sr.agent_io import dump_corpus
    dump_corpus(_corpus(), tmp_path / "corpus.json")

    def fake_parse(schema, system, user, **kw):
        return ScreeningBatch(results=[
            ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE, reason="eligible", confidence=0.9,
                            modality="CAR-T"),
            ScreeningResult(pmid="999", decision=ScreenDecision.INCLUDE, reason="hallucinated", confidence=0.9),
        ])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)

    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")

    screening = json.loads(out.read_text())
    includes = json.loads((tmp_path / "includes.json").read_text())
    by_pmid = {row["pmid"]: row for row in screening}
    assert out == tmp_path / "screening_merged.json"
    assert set(by_pmid) == {"111", "222"}
    assert by_pmid["222"]["decision"] == "uncertain"
    assert includes == ["111"]


def test_extract_writes_merged_and_indexed_outputs(monkeypatch, tmp_path):
    (tmp_path / "records_for_agents.json").write_text(json.dumps([
        {"pmid": "111", "title": "Trial 1", "abstract": "ORR 84%."},
    ]))
    (tmp_path / "includes.json").write_text(json.dumps(["111"]))
    (tmp_path / "screening_merged.json").write_text(json.dumps([
        {"pmid": "111", "decision": "include", "reason": "eligible", "confidence": 0.9},
    ]))

    def fake_extract(citation, cfg):
        return ExtractedStudy(pmid=citation.id, values=[
            ExtractedValue(field="complete_response_rate", group="efficacy", value="84%", quote="ORR 84%"),
            ExtractedValue(field="total_n", group="population", value="100", quote="100 patients"),
        ])

    monkeypatch.setattr("core.sr.extract.extract_study", fake_extract)

    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("extract", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")

    data = json.loads(out.read_text())
    assert out == tmp_path / "extractions.json"
    assert [s["pmid"] for s in data["extractions"]] == ["111"]
    assert (tmp_path / "extract_idx_000.json").exists()


def test_pool_proportions_runs_offline_without_r(monkeypatch, tmp_path):
    # Deterministic tool degrades honestly when R is absent (no crash).
    import core.sr.stats.meta as meta
    monkeypatch.setattr(meta, "r_available", lambda: False)
    (tmp_path / "extractions.json").write_text(json.dumps({"studies": [
        {"pmid": "1", "values": [{"field": "complete_response_rate", "group": "efficacy",
                                  "value": "84%"}, {"field": "total_n", "group": "population",
                                  "value": "100"}]}]}))
    from core.tools.runner import run_tool
    out = run_tool("pool-proportions", tmp_path, {"vertical": "mm-bcma-3l"})
    data = json.loads(out.read_text())
    assert data["field"] == "complete_response_rate" and data["available"] is False


def test_pool_proportions_without_field_uses_vertical_first_proportion(monkeypatch, tmp_path):
    import core.sr.config as config
    import core.sr.stats.meta as meta

    class Cfg:
        subgroup_field = ""
        subgroup_map = {}

        def poolable_fields(self):
            return {"proportion": [("primary_pct", "Primary percent"), ("secondary_pct", "Secondary")],
                    "log_effect": []}

    monkeypatch.setattr(config, "load_sr_config", lambda name=None: Cfg())
    monkeypatch.setattr(meta, "r_available", lambda: False)
    (tmp_path / "extractions.json").write_text(json.dumps({"studies": [
        {"pmid": "1", "values": [{"field": "primary_pct", "group": "efficacy", "value": "84%"},
                                  {"field": "total_n", "group": "population", "value": "100"}]}]}))

    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("pool-proportions", tmp_path, {})
    assert json.loads(out.read_text())["field"] == "primary_pct"


@pytest.mark.parametrize("payload", [
    [{"pmid": "1", "values": []}],
    {"studies": [{"pmid": "1", "values": []}]},
    {"extractions": [{"pmid": "1", "values": []}]},
])
def test_pool_proportions_accepts_supported_extraction_payloads(monkeypatch, tmp_path, payload):
    import core.sr.stats.meta as meta
    monkeypatch.setattr(meta, "r_available", lambda: False)
    (tmp_path / "extractions.json").write_text(json.dumps(payload))

    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("pool-proportions", tmp_path, {"field": "complete_response_rate"})
    assert json.loads(out.read_text())["available"] is False


def test_pool_proportions_uses_screening_modality_and_vertical_subgroups(monkeypatch, tmp_path):
    import core.sr.stats.meta as meta

    captured = {}

    def fake_run_metaprop(rows, label, png_path):
        captured["modalities"] = [r.modality for r in rows]
        return [SimpleNamespace(group="BsAb", k=2, prop=80.0, ci_lb=70.0, ci_ub=90.0, I2=0.0)]

    monkeypatch.setattr(meta, "r_available", lambda: True)
    monkeypatch.setattr(meta, "run_metaprop", fake_run_metaprop)
    (tmp_path / "screening_merged.json").write_text(json.dumps([
        {"pmid": "1", "decision": "include", "reason": "eligible", "confidence": 0.9,
         "modality": "bispecific antibody"},
        {"pmid": "2", "decision": "include", "reason": "eligible", "confidence": 0.9,
         "modality": "bispecific antibody"},
    ]))
    (tmp_path / "extractions.json").write_text(json.dumps({"extractions": [
        {"pmid": "1", "values": [
            {"field": "complete_response_rate", "group": "efficacy", "value": "80%"},
            {"field": "total_n", "group": "population", "value": "100"},
        ]},
        {"pmid": "2", "values": [
            {"field": "complete_response_rate", "group": "efficacy", "value": "82%"},
            {"field": "total_n", "group": "population", "value": "100"},
        ]},
    ]}))

    from core.tools.runner import run_tool
    registry.load_builtins()
    run_tool("pool-proportions", tmp_path, {"vertical": "mm-bcma-3l", "field": "complete_response_rate"})
    assert captured["modalities"] == ["BsAb", "BsAb"]


class _PairedCfg:
    subgroup_field = ""
    subgroup_map = {}
    dose_outcome = ""
    synthesis_arms = []

    def poolable_fields(self):
        return {"proportion": [("complete_response_rate", "Complete response rate")],
                "log_effect": []}

    def binary_2x2_fields(self):
        return [{
            "field": "response_2x2",
            "label": "Response 2x2",
            "pair": {"events_tx": "tx_events", "n_tx": "tx_n",
                     "events_ctrl": "ctrl_events", "n_ctrl": "ctrl_n"},
            "beneficial": True,
        }]

    def continuous_fields(self):
        return [{
            "field": "qol_score",
            "label": "Quality of life score",
            "pair": {"n_tx": "tx_n_cont", "mean_tx": "tx_mean", "sd_tx": "tx_sd",
                     "median_tx": "tx_median", "q1_tx": "tx_q1", "q3_tx": "tx_q3",
                     "n_ctrl": "ctrl_n_cont", "mean_ctrl": "ctrl_mean", "sd_ctrl": "ctrl_sd",
                     "median_ctrl": "ctrl_median", "q1_ctrl": "ctrl_q1", "q3_ctrl": "ctrl_q3"},
        }]


def _write_paired_extractions(tmp_path):
    (tmp_path / "extractions.json").write_text(json.dumps({"extractions": [
        {"pmid": "1", "values": [
            {"field": "tx_events", "group": "binary", "value": "30"},
            {"field": "tx_n", "group": "binary", "value": "100"},
            {"field": "ctrl_events", "group": "binary", "value": "20"},
            {"field": "ctrl_n", "group": "binary", "value": "100"},
            {"field": "tx_n_cont", "group": "continuous", "value": "40"},
            {"field": "tx_mean", "group": "continuous", "value": "1.2"},
            {"field": "tx_sd", "group": "continuous", "value": "0.4"},
            {"field": "ctrl_n_cont", "group": "continuous", "value": "38"},
            {"field": "ctrl_mean", "group": "continuous", "value": "0.9"},
            {"field": "ctrl_sd", "group": "continuous", "value": "0.5"},
            {"field": "age", "group": "moderator", "value": "60"},
        ]},
        {"pmid": "2", "values": [
            {"field": "tx_events", "group": "binary", "value": "24"},
            {"field": "tx_n", "group": "binary", "value": "80"},
            {"field": "ctrl_events", "group": "binary", "value": "16"},
            {"field": "ctrl_n", "group": "binary", "value": "80"},
            {"field": "tx_n_cont", "group": "continuous", "value": "36"},
            {"field": "tx_mean", "group": "continuous", "value": "1.1"},
            {"field": "tx_sd", "group": "continuous", "value": "0.4"},
            {"field": "ctrl_n_cont", "group": "continuous", "value": "36"},
            {"field": "ctrl_mean", "group": "continuous", "value": "0.8"},
            {"field": "ctrl_sd", "group": "continuous", "value": "0.5"},
            {"field": "age", "group": "moderator", "value": "62"},
        ]},
    ]}))


def test_metabin_and_metacont_write_honest_unavailable_artifacts(monkeypatch, tmp_path):
    import core.sr.config as config
    import core.sr.stats.meta as meta

    _write_paired_extractions(tmp_path)
    monkeypatch.setattr(config, "load_sr_config", lambda name=None: _PairedCfg())
    monkeypatch.setattr(meta, "metabin_available", lambda: False)
    monkeypatch.setattr(meta, "metacont_available", lambda: False)

    from core.tools.runner import run_tool
    registry.load_builtins()
    bin_out = run_tool("metabin", tmp_path, {"field": "response_2x2"})
    cont_out = run_tool("metacont", tmp_path, {"field": "qol_score"})

    binary = json.loads(bin_out.read_text())
    continuous = json.loads(cont_out.read_text())
    assert binary["available"] is False and binary["n_pooled"] == 2
    assert "R/meta" in binary["note"]
    assert continuous["available"] is False and continuous["n_pooled"] == 2
    assert continuous["n_imputed"] == 0
    assert "R/meta" in continuous["note"]


def test_metacont_reports_imputation_counts(monkeypatch, tmp_path):
    import core.sr.config as config
    import core.sr.stats.meta as meta

    (tmp_path / "extractions.json").write_text(json.dumps({"extractions": [
        {"pmid": "1", "values": [
            {"field": "tx_n_cont", "group": "continuous", "value": "50"},
            {"field": "tx_median", "group": "continuous", "value": "10"},
            {"field": "tx_q1", "group": "continuous", "value": "8"},
            {"field": "tx_q3", "group": "continuous", "value": "14"},
            {"field": "ctrl_n_cont", "group": "continuous", "value": "48"},
            {"field": "ctrl_median", "group": "continuous", "value": "7"},
            {"field": "ctrl_q1", "group": "continuous", "value": "5"},
            {"field": "ctrl_q3", "group": "continuous", "value": "9"},
        ]},
        {"pmid": "2", "values": [
            {"field": "tx_n_cont", "group": "continuous", "value": "36"},
            {"field": "tx_mean", "group": "continuous", "value": "1.1"},
            {"field": "tx_sd", "group": "continuous", "value": "0.4"},
            {"field": "ctrl_n_cont", "group": "continuous", "value": "36"},
            {"field": "ctrl_mean", "group": "continuous", "value": "0.8"},
            {"field": "ctrl_sd", "group": "continuous", "value": "0.5"},
        ]},
    ]}))
    monkeypatch.setattr(config, "load_sr_config", lambda name=None: _PairedCfg())
    monkeypatch.setattr(meta, "metacont_available", lambda: False)

    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("metacont", tmp_path, {"field": "qol_score"})
    data = json.loads(out.read_text())
    assert data["n_imputed"] == 1
    assert data["imputation_methods"]["Wan 2014/Luo 2018 median/IQR"] == 1


def test_meta_regression_honesty_gates_and_r2_passthrough(monkeypatch, tmp_path):
    import core.sr.config as config
    import core.sr.stats.meta as meta
    from core.sr.stats.meta import MetaRegResult, MetaRegTerm

    def write_rows(n):
        studies = []
        for i in range(n):
            studies.append({"pmid": str(i), "values": [
                {"field": "tx_n_cont", "group": "continuous", "value": "40"},
                {"field": "tx_mean", "group": "continuous", "value": str(1.0 + i * 0.05)},
                {"field": "tx_sd", "group": "continuous", "value": "0.4"},
                {"field": "ctrl_n_cont", "group": "continuous", "value": "40"},
                {"field": "ctrl_mean", "group": "continuous", "value": "0.8"},
                {"field": "ctrl_sd", "group": "continuous", "value": "0.5"},
                {"field": "age", "group": "moderator", "value": str(50 + i)},
            ]})
        (tmp_path / "extractions.json").write_text(json.dumps({"extractions": studies}))

    monkeypatch.setattr(config, "load_sr_config", lambda name=None: _PairedCfg())
    monkeypatch.setattr(meta, "metareg_available", lambda: True)

    def fake_run(rows, moderator, min_k=10):
        return MetaRegResult(
            True, len(rows) >= min_k,
            "" if len(rows) >= min_k else f"k={len(rows)} below the recommended >={min_k}",
            len(rows),
            [MetaRegTerm("(Intercept)", 0.1, 0.05, 0.0, 0.2, 0.04),
             MetaRegTerm(moderator, -0.02, 0.01, -0.04, -0.01, 0.01)],
            QM=6.0, QM_p=0.01, QE=3.0, QE_p=0.2, I2_resid=12.0, R2=-4.5,
        )

    monkeypatch.setattr(meta, "run_metareg", fake_run)
    from core.tools.runner import run_tool
    registry.load_builtins()

    write_rows(3)
    small = json.loads(run_tool("meta-regression", tmp_path, {
        "outcome": "qol_score", "moderator": "age", "min_k": 2}).read_text())
    assert small["available"] is False and "k<4" in small["note"]

    write_rows(6)
    exploratory = json.loads(run_tool("meta-regression", tmp_path, {
        "outcome": "qol_score", "moderator": "age", "min_k": 10}).read_text())
    assert exploratory["available"] is True
    assert exploratory["reliable"] is False
    assert exploratory["R2"] == -4.5
    assert "recommended >=10" in exploratory["note"]

    write_rows(10)
    reliable = json.loads(run_tool("meta-regression", tmp_path, {
        "outcome": "qol_score", "moderator": "age", "min_k": 10}).read_text())
    assert reliable["available"] is True and reliable["reliable"] is True


def _write_minimal_tool_run(tmp_path):
    from core.sr.agent_io import dump_corpus

    dump_corpus(_corpus(), tmp_path / "corpus.json")
    (tmp_path / "records_for_agents.json").write_text(json.dumps([
        {"pmid": "111", "title": "Trial 1", "abstract": "ORR 84%.", "year": 2024},
        {"pmid": "222", "title": "Trial 2", "abstract": "Screened out.", "year": 2024},
    ]))
    (tmp_path / "screening_merged.json").write_text(json.dumps([
        {"pmid": "111", "decision": "include", "reason": "eligible", "confidence": 0.9,
         "modality": "CAR-T", "study_design": "RCT"},
        {"pmid": "222", "decision": "exclude", "reason": "wrong intervention", "confidence": 0.9},
    ]))
    (tmp_path / "includes.json").write_text(json.dumps(["111"]))
    (tmp_path / "extractions.json").write_text(json.dumps({"extractions": [
        {"pmid": "111", "study_name": "TRIAL1", "modality": "CAR-T", "values": [
            {"field": "orr", "group": "efficacy", "value": "84%", "quote": "ORR 84%"},
            {"field": "complete_response_rate", "group": "efficacy", "value": "50%",
             "quote": "CR 50%"},
            {"field": "total_n", "group": "population", "value": "100", "quote": "100 patients"},
        ]},
    ]}))


def test_risk_of_bias_writes_aggregate_and_indexed_outputs(monkeypatch, tmp_path):
    _write_minimal_tool_run(tmp_path)

    def fake_parse(schema, system, user, **kw):
        assert kw["stage"] == "risk_of_bias"
        return RoBAssessment(
            pmid="111",
            tool="rob2",
            study_design="RCT",
            overall="Low",
            domains=[RoBDomain(key="randomization", judgment="Low", rationale="ok")],
        )

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    from core.tools.runner import run_tool
    registry.load_builtins()
    out = run_tool("risk-of-bias", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")
    data = json.loads(out.read_text())
    assert data["assessments"][0]["pmid"] == "111"
    assert (tmp_path / "rob_idx_000.json").exists()


def test_grade_and_manuscript_share_persisted_lock(monkeypatch, tmp_path):
    from core.sr.manuscript.assemble import build_components
    from core.sr.manuscript.render_md import components_to_md
    from core.sr.signoff import DRAFT_CERTAINTY

    _write_minimal_tool_run(tmp_path)
    (tmp_path / "rob.json").write_text(json.dumps({"assessments": []}))
    captured = {}

    def fake_export_project(run, cfg, records, **kwargs):
        comps = build_components(run, cfg, records, grade=kwargs.get("grade"),
                                 narrative={"abstract_conclusions": "Final conclusion.",
                                            "abstract_background": "BG",
                                            "abstract_results": "Results.",
                                            "introduction": "Intro",
                                            "discussion": "Discussion"})
        captured["md"] = components_to_md(comps)
        return {"project_dir": str(tmp_path / "project"), "warnings": [], "n_components": len(comps)}

    monkeypatch.setattr("core.sr.manuscript.export.export_project", fake_export_project)

    from core.tools.runner import run_tool
    registry.load_builtins()
    grade_out = run_tool("grade", tmp_path, {"vertical": "mm-bcma-3l"})
    grade = json.loads(grade_out.read_text())
    assert grade["rendered_rows"][0]["rendered_certainty"] == DRAFT_CERTAINTY
    run_tool("manuscript", tmp_path, {"vertical": "mm-bcma-3l"})
    assert DRAFT_CERTAINTY in captured["md"]
    assert "Final conclusion." not in captured["md"]

    grade_out = run_tool("grade", tmp_path, {"vertical": "mm-bcma-3l",
                                             "approve_conclusions": "Reviewer 2"})
    grade = json.loads(grade_out.read_text())
    assert grade["rendered_rows"][0]["rendered_certainty"] == grade["rows"][0]["certainty"]
    run_tool("manuscript", tmp_path, {"vertical": "mm-bcma-3l",
                                      "approve_conclusions": "Reviewer 2"})
    assert "Final conclusion." in captured["md"]


def test_tool_chain_runs_offline_search_screen_extract_pool(monkeypatch, tmp_path):
    async def fake_gather(cfg, retmax=None):
        return _corpus()

    def fake_parse(schema, system, user, **kw):
        return ScreeningBatch(results=[
            ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE, reason="eligible", confidence=0.9,
                            modality="CAR-T"),
            ScreeningResult(pmid="222", decision=ScreenDecision.EXCLUDE, reason="wrong intervention",
                            confidence=0.9),
        ])

    def fake_extract(citation, cfg):
        return ExtractedStudy(pmid=citation.id, modality="CAR-T", values=[
            ExtractedValue(field="complete_response_rate", group="efficacy", value="84%", quote="ORR 84%"),
            ExtractedValue(field="total_n", group="population", value="100", quote="100 patients"),
            ExtractedValue(field="tx_events", group="binary", value="30"),
            ExtractedValue(field="tx_n", group="binary", value="100"),
            ExtractedValue(field="ctrl_events", group="binary", value="20"),
            ExtractedValue(field="ctrl_n", group="binary", value="100"),
            ExtractedValue(field="tx_n_cont", group="continuous", value="40"),
            ExtractedValue(field="tx_mean", group="continuous", value="1.2"),
            ExtractedValue(field="tx_sd", group="continuous", value="0.4"),
            ExtractedValue(field="ctrl_n_cont", group="continuous", value="38"),
            ExtractedValue(field="ctrl_mean", group="continuous", value="0.9"),
            ExtractedValue(field="ctrl_sd", group="continuous", value="0.5"),
            ExtractedValue(field="age", group="moderator", value="60"),
        ])

    import core.sr.pipeline as sr_pipeline
    import core.sr.stats.meta as meta
    monkeypatch.setattr(sr_pipeline, "gather_corpus", fake_gather)
    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    monkeypatch.setattr("core.sr.extract.extract_study", fake_extract)
    monkeypatch.setattr(meta, "r_available", lambda: False)

    from core.tools.runner import run_tool
    registry.load_builtins()
    run_tool("search", tmp_path, {"vertical": "mm-bcma-3l", "retmax": 2})
    run_tool("screen", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")
    run_tool("extract", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")
    out = run_tool("pool-proportions", tmp_path, {"vertical": "mm-bcma-3l",
                                                  "field": "complete_response_rate"})
    import core.sr.config as config
    monkeypatch.setattr(config, "load_sr_config", lambda name=None: _PairedCfg())
    bin_out = run_tool("metabin", tmp_path, {"field": "response_2x2"})
    cont_out = run_tool("metacont", tmp_path, {"field": "qol_score"})
    metareg_out = run_tool("meta-regression", tmp_path, {"outcome": "qol_score", "moderator": "age"})

    assert (tmp_path / "corpus.json").exists()
    assert (tmp_path / "records_for_agents.json").exists()
    assert (tmp_path / "screening_merged.json").exists()
    assert json.loads((tmp_path / "includes.json").read_text()) == ["111"]
    assert (tmp_path / "extract_idx_000.json").exists()
    stats = json.loads(out.read_text())
    assert stats["available"] is False
    assert "need R/metafor" in stats["note"]
    assert json.loads(bin_out.read_text())["available"] is False
    assert json.loads(cont_out.read_text())["available"] is False
    assert json.loads(metareg_out.read_text())["available"] is False


def test_full_review_recipe_uses_configured_stats_for_narrative_and_manuscript(monkeypatch, tmp_path):
    from core.models import ClaimCheck
    from core.sr.stats.meta import PoolResult
    from core.sr.stats.run import StatsOutcome, StatsResult

    async def fake_gather(cfg, retmax=None):
        return _corpus()

    async def fake_harvest(pmids, doi_by_pmid, dest, concurrency=4):
        return []

    def fake_extract(citation, cfg):
        return ExtractedStudy(pmid=citation.id, modality="CAR-T", values=[
            ExtractedValue(field="complete_response_rate", group="efficacy", value="84%",
                           quote="ORR 84%"),
            ExtractedValue(field="orr", group="efficacy", value="84%", quote="ORR 84%"),
            ExtractedValue(field="total_n", group="population", value="100", quote="100 patients"),
        ])

    def fake_meta_analyze(extractions, modality_by_pmid, fig_dir, cfg=None, **kw):
        return StatsResult(
            True,
            "configured sentinel",
            outcomes=[StatsOutcome(
                "complete_response_rate", "Complete response rate",
                [PoolResult("ALL", 1, 84.0, 70.0, 90.0, 0.0, 0.0)],
                1, 0,
            )],
        )

    captured = {}

    def fake_export_project(run, cfg, records, **kwargs):
        captured["stats_note"] = kwargs["stats"].note
        return {"project_dir": str(tmp_path / "project"), "warnings": [], "n_components": 1}

    def fake_parse(schema, system, user, **kw):
        name = schema.__name__
        if name == "ScreeningBatch":
            return ScreeningBatch(results=[
                ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE,
                                reason="eligible", confidence=0.9, modality="CAR-T"),
                ScreeningResult(pmid="222", decision=ScreenDecision.EXCLUDE,
                                reason="wrong", confidence=0.9),
            ])
        if name == "RoBAssessment":
            return RoBAssessment(pmid="111", tool="nos", overall="Low", domains=[])
        if name == "_NarrativeDraft":
            assert "84.0%" in user
            return schema(
                abstract_background="BG [PMID: 111]",
                abstract_results="Configured pooling says 84.0% [PMID: 111]",
                abstract_conclusions="Conclusion [PMID: 111]",
                introduction="Intro [PMID: 111]",
                discussion="Discussion [PMID: 111]",
            )
        if schema is ClaimCheck:
            assert kw["stage"] == "claim_check"
            return ClaimCheck(finding_index=0, supported=True, notes="supported")
        raise AssertionError(name)

    import core.sr.pipeline as sr_pipeline
    import core.sr.stats.run as stats_run
    monkeypatch.setattr(sr_pipeline, "gather_corpus", fake_gather)
    monkeypatch.setattr("core.sr.fetch_oa.harvest_oa", fake_harvest)
    monkeypatch.setattr("core.sr.extract.extract_study", fake_extract)
    monkeypatch.setattr(stats_run, "meta_analyze", fake_meta_analyze)
    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    monkeypatch.setattr("core.sr.manuscript.export.export_project", fake_export_project)

    from core.tools import recipes
    registry.load_builtins()
    recipes.run_recipe("full-review", tmp_path, {
        "vertical": "mm-bcma-3l",
        "retmax": 2,
        "base_dir": str(tmp_path / "out"),
        "day": "2026-06-09",
    }, override="cloud")

    assert json.loads((tmp_path / "narrative.json").read_text())["stats_source"] == "stats_configured.json"
    assert captured["stats_note"] == "configured sentinel"


def _write_narrative_run_folder(tmp_path):
    from core.sr.agent_io import dump_corpus, records_for_agents

    corpus = _corpus()
    dump_corpus(corpus, tmp_path / "corpus.json")
    (tmp_path / "records_for_agents.json").write_text(
        json.dumps(records_for_agents(corpus), indent=2), encoding="utf-8")
    (tmp_path / "screening_merged.json").write_text(json.dumps([
        {"pmid": "111", "decision": "include", "reason": "eligible", "confidence": 0.9,
         "modality": "CAR-T", "study_design": "RCT"},
        {"pmid": "222", "decision": "exclude", "reason": "wrong", "confidence": 0.9},
    ]), encoding="utf-8")
    (tmp_path / "includes.json").write_text(json.dumps(["111"]), encoding="utf-8")
    (tmp_path / "extractions.json").write_text(json.dumps({"extractions": [{
        "pmid": "111",
        "modality": "CAR-T",
        "values": [
            {"field": "orr", "group": "efficacy", "value": "84%", "quote": "ORR 84%"},
            {"field": "total_n", "group": "population", "value": "100", "quote": "100 patients"},
        ],
    }]}), encoding="utf-8")
    (tmp_path / "rob.json").write_text(json.dumps({"assessments": [
        {"pmid": "111", "tool": "rob2", "overall": "Some concerns", "domains": []}
    ]}), encoding="utf-8")
    (tmp_path / "grade.json").write_text(json.dumps({"rows": [
        {"outcome": "ORR", "field": "orr", "n_studies": 1, "n_rct": 1,
         "start": "High", "downgrades": [], "deferred": [], "certainty": "High"}
    ]}), encoding="utf-8")
    (tmp_path / "stats_configured.json").write_text(json.dumps({
        "available": True, "note": "", "outcomes": [], "hr_outcomes": [],
        "bin_outcomes": [], "cont_outcomes": [], "meta_regression": None,
    }), encoding="utf-8")


def test_narrative_drops_unsupported_claims_and_writes_artifacts(monkeypatch, tmp_path):
    from core.models import ClaimCheck
    from core.tools.runner import run_tool

    _write_narrative_run_folder(tmp_path)
    captured = {}

    def fake_parse(schema, system, user, **kw):
        if schema.__name__ == "_NarrativeDraft":
            captured["system"] = system
            return schema(
                abstract_background="Eligible trial reported ORR [PMID: 111].",
                abstract_results="Eligible trial reported ORR [PMID: 111]. Unsupported certainty claim [PMID: 111].",
                abstract_conclusions="Unsupported conclusion [PMID: 111].",
                introduction="Eligible trial reported ORR [PMID: 111].",
                discussion="Unsupported RoB synthesis [PMID: 111]. Uncited synthesis claim.",
            )
        if schema is ClaimCheck:
            return ClaimCheck(
                finding_index=0,
                supported="Eligible trial" in user,
                notes="supported" if "Eligible trial" in user else "unsupported",
            )
        raise AssertionError(schema)

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    registry.load_builtins()
    out = run_tool("narrative", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")
    payload = json.loads(out.read_text())
    final = json.loads((tmp_path / "narrative_final.json").read_text())

    metrics = payload["claim_check"]
    assert metrics["sentences_considered"] == 7
    assert metrics["claim_checked"] == 6
    assert metrics["dropped_no_citation"] == 1
    assert metrics["dropped_unsupported"] == 3
    assert metrics["dropped_total"] == 4
    assert metrics["kept"] == 3
    assert metrics["sentences_considered"] == (
        metrics["kept"] + metrics["dropped_no_citation"] + metrics["dropped_unsupported"])
    assert "Do not write review-level synthesis" in captured["system"]
    assert "GRADE statements" in captured["system"]
    assert "risk-of-bias claims" in captured["system"]
    assert "omit the claim rather than speculating" in captured["system"]
    assert "Eligible trial reported ORR" in payload["cleaned"]["abstract_results"]
    assert "Unsupported" not in payload["cleaned"]["abstract_results"]
    assert final["abstract_conclusions"].startswith("CONCLUSIONS LOCKED")
    assert "Reviewer 2" in final["abstract_conclusions"]


def test_narrative_all_claims_dropped_falls_back_and_manuscript_assembles(monkeypatch, tmp_path):
    from core.models import ClaimCheck
    from core.tools.runner import run_tool

    _write_narrative_run_folder(tmp_path)

    def fake_parse(schema, system, user, **kw):
        if schema.__name__ == "_NarrativeDraft":
            return schema(
                abstract_background="Unsupported background [PMID: 111].",
                abstract_results="Unsupported results [PMID: 111].",
                abstract_conclusions="Unsupported conclusion [PMID: 111].",
                introduction="Unsupported intro [PMID: 111].",
                discussion="Unsupported discussion [PMID: 111].",
            )
        if schema is ClaimCheck:
            return ClaimCheck(finding_index=0, supported=False, notes="unsupported")
        raise AssertionError(schema)

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    monkeypatch.setattr("core.sr.manuscript.export.export_project",
                        lambda *a, **kw: {"project_dir": str(tmp_path / "project"),
                                          "warnings": [], "n_components": 1})
    registry.load_builtins()
    run_tool("narrative", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")
    payload = json.loads((tmp_path / "narrative.json").read_text())
    assert payload["claim_check"]["claim_checked"] == 5
    assert payload["claim_check"]["dropped_unsupported"] == 5
    assert payload["claim_check"]["dropped_total"] == 5
    assert "did not support AI-drafted narrative prose" in payload["cleaned"]["abstract_results"]

    out = run_tool("manuscript", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")
    assert json.loads(out.read_text())["n_components"] == 1


def test_narrative_invalid_pmid_still_fails_closed(monkeypatch, tmp_path):
    from core.tools.runner import run_tool

    _write_narrative_run_folder(tmp_path)

    def fake_parse(schema, system, user, **kw):
        if schema.__name__ == "_NarrativeDraft":
            return schema(abstract_results="This cites an invalid record [PMID: 999].")
        raise AssertionError(schema)

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    registry.load_builtins()
    with pytest.raises(ValueError, match="invalid PMIDs"):
        run_tool("narrative", tmp_path, {"vertical": "mm-bcma-3l"}, override="cloud")


def test_cli_list_and_describe(capsys):
    from core.tools.cli import main
    main(["list"])
    listing = capsys.readouterr().out
    assert "screen" in listing and "extract" in listing and "metabin" in listing
    assert "meta-regression" in listing and "risk-of-bias" in listing and "Posture:" in listing
    main(["describe", "extract"])
    desc = capsys.readouterr().out
    assert "reads:   records_for_agents.json" in desc and "would run on: cloud" in desc
    main(["describe", "manuscript"])
    desc = capsys.readouterr().out
    assert "conclusion_bearing: True" in desc
    main(["describe", "metabin"])
    desc = capsys.readouterr().out
    assert "writes:  stats_binary.json" in desc and "externally_validated=False" in desc
    main(["describe", "meta-regression"])
    desc = capsys.readouterr().out
    assert "writes:  stats_metareg.json" in desc and "externally_validated=False" in desc
    main(["recipe", "list"])
    recipes_out = capsys.readouterr().out
    assert "quick-look" in recipes_out and "full-review" in recipes_out
