"""Tool-platform foundation: registry, maturity posture, runner, safety routing."""

from __future__ import annotations

import asyncio
import contextvars
import json
from pathlib import Path

import pytest

from core.llm import client
from core.models import Citation, IdType, RetrievalResult
from core.render import provenance as provenance_store
from core.sr.pipeline import Corpus, build_run
from core.tools import registry, runner
from core.tools.posture import resolve_backend
from core.tools.spec import BackendPolicy, Scorecard, Tool


def _tool(name="t", kind="ai", inputs=(), output="out.json", run=None, backend=None, score=None):
    return Tool(name=name, description="d", kind=kind, input_artifacts=tuple(inputs),
                output_artifact=output, run=run or (lambda rd, a, b: rd / output),
                backend=backend or BackendPolicy(), scorecard=score or Scorecard())


# --- spec validation -------------------------------------------------------
def test_tool_kind_must_be_valid():
    with pytest.raises(ValueError):
        _tool(kind="magic")


def test_deterministic_tool_cannot_be_cloud_only():
    with pytest.raises(ValueError):
        _tool(kind="deterministic", backend=BackendPolicy(cloud_only=True))


# --- registry --------------------------------------------------------------
def test_register_get_and_duplicate(monkeypatch):
    monkeypatch.setattr(registry, "_TOOLS", {})
    t = registry.register(_tool(name="alpha"))
    assert registry.get("alpha") is t
    assert [x.name for x in registry.all_tools()] == ["alpha"]
    with pytest.raises(ValueError):
        registry.register(_tool(name="alpha"))
    with pytest.raises(KeyError):
        registry.get("missing")


# --- posture / backend resolution ------------------------------------------
def test_deterministic_tool_has_no_brain():
    assert resolve_backend(_tool(kind="deterministic"), posture="local") == "n/a"


def test_posture_cloud_and_local():
    ai = _tool()
    assert resolve_backend(ai, posture="cloud") == "cloud"
    assert resolve_backend(ai, posture="local") == "local"


def test_prefer_local_gated_on_validation():
    unproven = _tool(backend=BackendPolicy(local_validated=False))
    proven = _tool(backend=BackendPolicy(local_validated=True))
    assert resolve_backend(unproven, posture="prefer-local") == "cloud"
    assert resolve_backend(proven, posture="prefer-local") == "local"


def test_cloud_only_tool_never_local():
    safety = _tool(backend=BackendPolicy(cloud_only=True))
    assert resolve_backend(safety, posture="local") == "cloud"
    with pytest.raises(ValueError, match="cloud-only"):
        resolve_backend(safety, override="local")
    with pytest.raises(ValueError, match="cloud-only"):
        resolve_backend(safety, override="harness")


def test_harness_routing_is_gated_by_tool_policy():
    unsupported = _tool(name="narrative", backend=BackendPolicy())
    supported = _tool(name="screen", backend=BackendPolicy(harness_enabled=True))
    assert resolve_backend(supported, posture="harness") == "harness"
    assert resolve_backend(unsupported, posture="harness") == "cloud"
    with pytest.raises(ValueError, match="harness not yet supported for 'narrative'"):
        resolve_backend(unsupported, override="harness")


def test_override_wins():
    ai = _tool()
    assert resolve_backend(ai, override="local", posture="cloud") == "local"
    assert resolve_backend(ai, override="cloud", posture="local") == "cloud"


# --- runner ----------------------------------------------------------------
def test_runner_errors_on_missing_input(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_TOOLS", {})
    registry.register(_tool(name="needscorpus", inputs=("corpus.json",)))
    with pytest.raises(FileNotFoundError, match="upstream"):
        runner.run_tool("needscorpus", tmp_path)


def test_runner_pins_backend_for_ai_tool(monkeypatch, tmp_path):
    seen = {}

    def _run(run_dir, args, backend):
        seen["backend"] = backend
        seen["routed_screening"] = client.backend_for("screening")
        seen["routed_claimcheck"] = client.backend_for("claim_check")
        out = run_dir / "out.json"
        out.write_text(json.dumps({"ok": True}))
        return out

    monkeypatch.setattr(registry, "_TOOLS", {})
    registry.register(_tool(name="airun", run=_run))
    out = runner.run_tool("airun", tmp_path, override="local")
    assert out.exists() and seen["backend"] == "local"
    assert seen["routed_screening"] == "ollama"        # forced local applies
    assert seen["routed_claimcheck"] == "anthropic"    # safety net stays cloud
    assert client.current_forced_backend() is None     # cleared after the call


def test_claim_check_cannot_be_forced_harness():
    client.set_forced_backend("harness")
    try:
        assert client.backend_for("screening") == "harness"
        assert client.backend_for("claim_check") == "anthropic"
    finally:
        client.clear_forced_backend()


def test_llm_context_state_isolated_between_contexts():
    def _ctx_a():
        client.reset_stage_log()
        client.set_forced_backend("local")
        client._record_stage("screening", "model-a", "ollama")
        return client.backend_for("screening"), client.stage_log()

    def _ctx_b():
        client.reset_stage_log()
        client.set_forced_backend("cloud")
        client._record_stage("extraction", "model-b", "anthropic")
        return client.backend_for("screening"), client.stage_log()

    a_backend, a_log = contextvars.Context().run(_ctx_a)
    b_backend, b_log = contextvars.Context().run(_ctx_b)

    assert a_backend == "ollama"
    assert b_backend == "anthropic"
    assert [e["stage"] for e in a_log] == ["screening"]
    assert [e["stage"] for e in b_log] == ["extraction"]


@pytest.mark.asyncio
async def test_stage_log_accumulates_from_child_async_tasks():
    client.reset_stage_log()

    async def _record(stage):
        client._record_stage(stage, "test-model", "anthropic")

    await asyncio.gather(_record("screening"), _record("extraction"))
    log = client.stage_log()
    assert {e["stage"] for e in log} == {"screening", "extraction"}
    assert log


@pytest.mark.asyncio
async def test_async_stage_log_is_snapshotted_into_provenance():
    from core.sr.config import load_sr_config

    cfg = load_sr_config("mm-bcma-3l")
    citation = Citation(id_type=IdType.PMID, id="111", source="pubmed",
                        title="T", snippet="A", verified=True)
    corpus = Corpus(
        cfg=cfg,
        run_id="tool-context-run",
        search_result=RetrievalResult(source="pubmed", query="q", citations=[citation]),
        identified=[citation],
        deduped=[citation],
        verified=[citation],
        rejected=[],
        seed_verification={},
        per_source={"pubmed": 1},
        sources_executed=["pubmed"],
    )
    client.reset_stage_log()

    async def _record(stage):
        client._record_stage(stage, "test-model", "anthropic")

    await asyncio.gather(_record("screening"), _record("extraction"))
    run = build_run(corpus, screening=[], extractions=[])
    prov = provenance_store.read(run.provenance_id)
    assert prov is not None
    assert {e["stage"] for e in prov.stage_models} == {"screening", "extraction"}


# --- scorecard / B->D gate -------------------------------------------------
def test_trusted_for_agent_needs_both_checks():
    assert Scorecard(internally_tested=True, externally_validated=True).trusted_for_agent
    assert not Scorecard(internally_tested=True).trusted_for_agent
    assert not Scorecard(externally_validated=True).trusted_for_agent
