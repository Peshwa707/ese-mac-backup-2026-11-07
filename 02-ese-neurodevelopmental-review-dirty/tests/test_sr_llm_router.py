"""Phase 2: tiered LLM router + local (Ollama) structured-output backend.

The contract every backend must honor: schema in -> validated Pydantic out, or a
hard RuntimeError. The router picks the backend per (stage, SR_LLM_PROFILE); the
adversarial claim-check NEVER goes local.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import BaseModel

from core.llm import client


class _Mini(BaseModel):
    decision: str
    confidence: float


def test_backend_routing_by_profile(monkeypatch):
    monkeypatch.setenv("SR_LLM_PROFILE", "cloud")
    assert client.backend_for("screening") == "anthropic"

    monkeypatch.setenv("SR_LLM_PROFILE", "hybrid")
    assert client.backend_for("screening") == "ollama"
    assert client.backend_for("extraction") == "ollama"
    assert client.backend_for("synthesis") == "anthropic"
    assert client.backend_for("claim_check") == "anthropic"

    monkeypatch.setenv("SR_LLM_PROFILE", "local")
    assert client.backend_for("synthesis") == "ollama"
    # Safety net: claim-check stays cloud even in the all-local profile.
    assert client.backend_for("claim_check") == "anthropic"

    monkeypatch.setenv("SR_LLM_PROFILE", "nonsense")   # unknown -> cloud
    assert client.backend_for("screening") == "anthropic"


def test_parse_routes_local_stage_to_ollama_and_records(monkeypatch):
    sentinel = _Mini(decision="include", confidence=0.9)
    monkeypatch.setenv("SR_LLM_PROFILE", "hybrid")
    monkeypatch.setattr(client, "_ollama_parse", lambda *a, **k: sentinel)
    client.reset_stage_log()
    out = client.parse(_Mini, "sys", "usr", stage="screening")
    assert out is sentinel
    rec = next(e for e in client.stage_log() if e["stage"] == "screening")
    assert rec["backend"] == "ollama" and rec["model"] == client.OLLAMA_MODEL


def test_parse_falls_back_to_cloud_when_local_fails(monkeypatch):
    sentinel = _Mini(decision="cloud", confidence=0.5)

    class _Resp:
        parsed_output = sentinel

    class _Fake:
        class messages:
            @staticmethod
            def parse(**kwargs):
                return _Resp()

    monkeypatch.setenv("SR_LLM_PROFILE", "hybrid")
    monkeypatch.setattr(client, "_ollama_parse",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("local fail")))
    monkeypatch.setattr(client, "_get_client", lambda: _Fake())
    client.reset_stage_log()
    out = client.parse(_Mini, "sys", "usr", stage="screening")   # local stage, local errors
    assert out is sentinel                                        # served by cloud fallback
    rec = next(e for e in client.stage_log() if e["stage"] == "screening")
    assert rec["backend"] == "anthropic (local-fallback)"


def test_screening_confidence_rescaled_from_local_scale():
    # Local models emit confidence on 0-10 / 0-100 scales; the validator rescales+clamps.
    from core.sr.models import ScreeningResult

    def conf(v):
        return ScreeningResult(pmid="1", decision="include", reason="x", confidence=v).confidence

    assert conf(9) == 0.9            # 0-10 scale
    assert conf(95) == 0.95         # 0-100 scale
    assert conf(0.8) == 0.8         # already 0-1, untouched
    assert conf(500) == 1.0         # clamped
    assert conf("bad") == 0.0       # non-numeric -> 0


def test_parse_cloud_stage_uses_anthropic(monkeypatch):
    sentinel = _Mini(decision="x", confidence=0.1)

    class _Resp:
        parsed_output = sentinel

    class _Fake:
        class messages:
            @staticmethod
            def parse(**kwargs):
                return _Resp()

    monkeypatch.setenv("SR_LLM_PROFILE", "hybrid")
    monkeypatch.setattr(client, "_get_client", lambda: _Fake())
    client.reset_stage_log()
    out = client.parse(_Mini, "sys", "usr", stage="synthesis")   # cloud stage in hybrid
    assert out is sentinel
    rec = next(e for e in client.stage_log() if e["stage"] == "synthesis")
    assert rec["backend"] == "anthropic"


@respx.mock
def test_ollama_parse_validates_into_pydantic(monkeypatch):
    monkeypatch.setattr(client, "OLLAMA_MODEL", "qwen2.5")
    respx.post(f"{client.OLLAMA_HOST}/api/chat").mock(return_value=httpx.Response(
        200, json={"message": {"content": '{"decision": "include", "confidence": 0.8}'}}))
    out = client._ollama_parse(_Mini, "sys", "usr")
    assert isinstance(out, _Mini) and out.decision == "include" and out.confidence == 0.8


@respx.mock
def test_ollama_parse_fails_closed_on_bad_output():
    respx.post(f"{client.OLLAMA_HOST}/api/chat").mock(return_value=httpx.Response(
        200, json={"message": {"content": "not json at all"}}))
    with pytest.raises(RuntimeError, match="schema-valid"):
        client._ollama_parse(_Mini, "sys", "usr")


@respx.mock
def test_ollama_parse_fails_closed_on_transport_error():
    respx.post(f"{client.OLLAMA_HOST}/api/chat").mock(return_value=httpx.Response(500))
    with pytest.raises(RuntimeError, match="failed"):
        client._ollama_parse(_Mini, "sys", "usr")


def _ollama_up() -> bool:
    try:
        r = httpx.get(f"{client.OLLAMA_HOST}/api/tags", timeout=2)
        return r.status_code == 200 and client.OLLAMA_MODEL in r.text
    except Exception:
        return False


@pytest.mark.live
@pytest.mark.skipif(not _ollama_up(), reason="Ollama not running / default model not pulled")
def test_live_local_screening_decision():
    """Real local backend: the validated default model returns a schema-valid,
    clinically-correct screening decision (smoke for the cost path)."""
    from core.sr.models import ScreeningResult

    sys = ("You are Reviewer 1 for a systematic review on BCMA CAR-T vs bispecific "
           "antibodies in relapsed/refractory multiple myeloma. INCLUDE prospective trials "
           "of BCMA CAR-T or BCMA bispecifics reporting efficacy; EXCLUDE preclinical/animal "
           "studies. Decide for ONE record.")
    usr = ("pmid=39551604; title=Ciltacabtagene autoleucel in relapsed myeloma: phase 2; "
           "abstract=BCMA CAR-T therapy, ORR 85%. Return pmid, decision, reason, confidence, "
           "modality, study_design.")
    r = client._ollama_parse(ScreeningResult, sys, usr, max_tokens=800)
    assert r.decision.value == "include"
