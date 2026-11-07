"""WP-M2.1 — provider-agnostic LLM gateway."""
import pickle

import pytest
from pydantic import BaseModel

from core.llm import client, gateway
from core.llm.gateway import ProviderSpec, SecretStr


class _Schema(BaseModel):
    ok: bool


def _reset_log():
    client.reset_stage_log()


def test_secretstr_never_reveals_or_serializes():
    s = SecretStr("sk-DEADBEEF0123456789")
    assert "DEADBEEF" not in repr(s) and "DEADBEEF" not in str(s)
    assert s.reveal() == "sk-DEADBEEF0123456789"
    with pytest.raises(TypeError):
        pickle.dumps(s)


def test_anthropic_spec_routes_to_native_parse_and_records(monkeypatch):
    _reset_log()
    captured = {}

    def _fake(schema, system, user, max_tokens, model=None, api_key=None):
        captured["model"] = model
        return _Schema(ok=True)

    monkeypatch.setattr(client, "_anthropic_parse", _fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = gateway.gateway_parse(_Schema, "sys", "usr", stage="screening",
                                spec=ProviderSpec(source="anthropic", model="claude-opus-4-8"))
    assert out.ok and captured["model"] == "claude-opus-4-8"
    log = client.stage_log()
    assert log and log[0]["model"] == "claude-opus-4-8" and log[0]["backend"] == "anthropic"


def test_local_spec_routes_to_ollama_with_model_and_host(monkeypatch):
    _reset_log()
    captured = {}

    def _fake(schema, system, user, *, max_tokens=16000, model=None, host=None):
        captured["model"], captured["host"] = model, host
        return _Schema(ok=True)

    monkeypatch.setattr(client, "_ollama_parse", _fake)
    gateway.gateway_parse(_Schema, "sys", "usr", stage="screening",
                          spec=ProviderSpec(source="local", model="llama3.1:70b",
                                            endpoint="http://localhost:11434"))
    assert captured["model"] == "llama3.1:70b" and captured["host"] == "http://localhost:11434"
    assert client.stage_log()[0]["backend"] == "ollama"


def test_api_provider_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("core.secrets.providers_file",lambda: __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(gateway.MissingKeyError):
        gateway.gateway_parse(_Schema, "s", "u", stage="screening",
                              spec=ProviderSpec(source="anthropic", model="claude-opus-4-8"))


def test_unsupported_source_fails_closed():
    with pytest.raises(ValueError, match="unsupported provider"):
        gateway.gateway_parse(_Schema, "s", "u", stage="x",
                              spec=ProviderSpec(source="openai", model="gpt-5"))


def test_load_key_refuses_world_readable_providers_file(tmp_path, monkeypatch):
    f = tmp_path / "providers.env"
    f.write_text("ANTHROPIC_API_KEY=sk-fromfile\n")
    f.chmod(0o644)                               # too open
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("core.secrets.providers_file",lambda: f)
    with pytest.raises(RuntimeError, match="permissions too open"):
        gateway.load_key("anthropic")


def test_load_key_reads_secure_providers_file(tmp_path, monkeypatch):
    f = tmp_path / "providers.env"
    f.write_text("ANTHROPIC_API_KEY=sk-fromfile\n")
    f.chmod(0o600)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("core.secrets.providers_file",lambda: f)
    key = gateway.load_key("anthropic")
    assert key is not None and key.reveal() == "sk-fromfile"


def test_load_key_local_needs_none():
    assert gateway.load_key("local") is None


def test_gateway_passes_file_key_to_anthropic_egress(tmp_path, monkeypatch):
    """A key present only in a 0600 providers.env must be wired into the actual call."""
    _reset_log()
    f = tmp_path / "providers.env"
    f.write_text("ANTHROPIC_API_KEY=sk-fromfile\n")
    f.chmod(0o600)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("core.secrets.providers_file",lambda: f)
    captured = {}

    def _fake(schema, system, user, max_tokens, model=None, api_key=None):
        captured["api_key"] = api_key
        return _Schema(ok=True)

    monkeypatch.setattr(client, "_anthropic_parse", _fake)
    gateway.gateway_parse(_Schema, "s", "u", stage="screening",
                          spec=ProviderSpec(source="anthropic", model="claude-opus-4-8"))
    assert captured["api_key"] == "sk-fromfile"          # file key reached egress


def test_load_key_refuses_symlinked_providers_file(tmp_path, monkeypatch):
    real = tmp_path / "real.env"
    real.write_text("ANTHROPIC_API_KEY=sk-x\n")
    real.chmod(0o600)
    link = tmp_path / "providers.env"
    link.symlink_to(real)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("core.secrets.providers_file",lambda: link)
    with pytest.raises(RuntimeError, match="symlink"):
        gateway.load_key("anthropic")


def test_load_key_refuses_wrong_owner(tmp_path, monkeypatch):
    f = tmp_path / "providers.env"
    f.write_text("ANTHROPIC_API_KEY=sk-x\n")
    f.chmod(0o600)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("core.secrets.providers_file",lambda: f)
    monkeypatch.setattr(gateway.os, "getuid", lambda: 999999, raising=False)  # not the file owner
    with pytest.raises(RuntimeError, match="owned by"):
        gateway.load_key("anthropic")


def test_empty_model_fails_closed():
    with pytest.raises(ValueError, match="model must be non-empty"):
        gateway.gateway_parse(_Schema, "s", "u", stage="x",
                              spec=ProviderSpec(source="local", model=""))
