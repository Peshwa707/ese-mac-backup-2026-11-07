"""Real-path tests for structured-output client helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from core.llm import client


class _Schema(BaseModel):
    ok: bool


@pytest.fixture(autouse=True)
def _reset_client_state():
    client.reset_stage_log()
    client._LAST_SERVED.set(None)


def _install_fake_openai(monkeypatch, *, response=None, error: Exception | None = None):
    import openai

    captured = {}

    class _FakeResponses:
        def parse(self, **kwargs):
            captured["kwargs"] = kwargs
            if error is not None:
                raise error
            return response

    class _FakeOpenAI:
        def __init__(self, api_key: str, base_url: str | None = None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.responses = _FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return captured


def test_openai_parse_returns_schema_instance_without_refusal(monkeypatch):
    parsed = _Schema(ok=True)
    resp = SimpleNamespace(
        output_parsed=parsed,
        output=[{"type": "message", "content": [{"type": "output_text"}]}],
        model="gpt-5.5-mini-2026-06-01",
        system_fingerprint="fp-openai",
    )
    captured = _install_fake_openai(monkeypatch, response=resp)

    out = client._openai_parse(_Schema, "sys", "usr", 123, model="gpt-5", api_key="sk-test")

    assert out is parsed
    assert captured["api_key"] == "sk-test"
    assert captured["kwargs"]["text_format"] is _Schema


def test_openai_parse_raises_when_sdk_returns_no_schema_valid_output(monkeypatch):
    resp = SimpleNamespace(output_parsed=None, output=[], model="gpt-5.5-mini")
    _install_fake_openai(monkeypatch, response=resp)

    with pytest.raises(RuntimeError, match="no schema-valid"):
        client._openai_parse(_Schema, "sys", "usr", 123, model="gpt-5", api_key="sk-test")


def test_openai_parse_raises_on_item_level_refusal_dict_output(monkeypatch):
    resp = SimpleNamespace(
        output_parsed=_Schema(ok=True),
        output=[{"type": "refusal"}],
        model="gpt-5.5-mini",
    )
    _install_fake_openai(monkeypatch, response=resp)

    with pytest.raises(RuntimeError, match="no schema-valid"):
        client._openai_parse(_Schema, "sys", "usr", 123, model="gpt-5", api_key="sk-test")


def test_openai_parse_raises_on_nested_content_refusal_attr_output(monkeypatch):
    resp = SimpleNamespace(
        output_parsed=_Schema(ok=True),
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])],
        model="gpt-5.5-mini",
    )
    _install_fake_openai(monkeypatch, response=resp)

    with pytest.raises(RuntimeError, match="no schema-valid"):
        client._openai_parse(_Schema, "sys", "usr", 123, model="gpt-5", api_key="sk-test")


def test_anthropic_parse_scrubs_sdk_exception_type(monkeypatch):
    class _Boom(RuntimeError):
        pass

    class _FakeMessages:
        @staticmethod
        def parse(**kwargs):
            raise _Boom("raw provider detail")

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(client, "_get_client", lambda: _FakeClient())

    with pytest.raises(RuntimeError, match=r"anthropic \(claude-opus-4-8\) call failed: _Boom"):
        client._anthropic_parse(_Schema, "sys", "usr", 123)


def test_record_stage_attaches_served_model_and_consumes_last_served(monkeypatch):
    parsed = _Schema(ok=True)
    resp = SimpleNamespace(
        output_parsed=parsed,
        output=[],
        model="gpt-5.5-mini-2026-06-01",
        system_fingerprint="fp-served",
    )
    _install_fake_openai(monkeypatch, response=resp)
    client.reset_stage_log()

    out = client._openai_parse(_Schema, "sys", "usr", 123, model="gpt-5", api_key="sk-test")
    client._record_stage("screening", "gpt-5", "openai")
    client._record_stage("synthesis", "gpt-5", "openai")

    log = client.stage_log()
    screening = next(e for e in log if e["stage"] == "screening")
    synthesis = next(e for e in log if e["stage"] == "synthesis")
    assert out is parsed
    assert screening["served"] == [{"served_model": "gpt-5.5-mini-2026-06-01", "fingerprint": "fp-served"}]
    assert "served" not in synthesis


def test_merge_served_dedups_identical_entries():
    entry = {"stage": "screening", "model": "gpt-5", "backend": "openai", "n_calls": 1}
    served = {"served_model": "gpt-5.5-mini-2026-06-01", "fingerprint": "fp-served"}

    client._merge_served(entry, served)
    client._merge_served(entry, served)

    assert entry["served"] == [served]
