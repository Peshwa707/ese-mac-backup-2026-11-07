"""Provider-agnostic LLM gateway (WP-M2.1).

Wraps the existing ``core.llm.client`` parse paths and adds per-(stage|reviewer)
provider/model selection so Reviewer 1 / Reviewer 2 (and any stage) can run on
different models. M2 supports ``anthropic`` (native structured-output path that
cite-or-omit relies on — preserved) and ``local`` (Ollama). OpenAI/Gemini plug into
the same seam later; NO LiteLLM dependency (supply-chain/telemetry surface deferred).

Keys are wrapped in ``SecretStr`` (never repr'd, str'd, or serialized) and read only
from the environment or a 0600 ``providers.env`` — never persisted.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

from core.llm import client

T = TypeVar("T", bound=BaseModel)

# Provider sources callable synchronously by the headless gateway. The subscription
# harness path (Claude Max / Codex) is the interactive agent flow, not this gateway.
_SUPPORTED = {"anthropic", "local"}
_API_SOURCES = {"anthropic"}                    # seam: add "openai", "gemini"
_ENV_KEY = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GOOGLE_API_KEY"}


class MissingKeyError(RuntimeError):
    """A configured API provider has no key available (fail closed)."""


class SecretStr:
    """An opaque key wrapper: never reveals via repr/str, and refuses serialization."""
    __slots__ = ("_v",)

    def __init__(self, v: str):
        self._v = v

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "SecretStr(***)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("SecretStr is not serializable")


@dataclass(frozen=True)
class ProviderSpec:
    source: str                  # "anthropic" | "local"
    model: str
    endpoint: str = ""           # local only (Ollama host URL)


def _providers_file() -> Path:
    env = os.environ.get("ONCO_PROVIDERS_ENV")
    return Path(env) if env else Path.home() / ".config" / "onco-engine" / "providers.env"


def load_key(provider: str) -> SecretStr | None:
    """Load an API key from env or a 0600 providers.env. ``local`` needs none."""
    if provider == "local":
        return None
    env_name = _ENV_KEY.get(provider, f"{provider.upper()}_API_KEY")
    val = os.environ.get(env_name)
    if val:
        return SecretStr(val)
    f = _providers_file()
    if f.exists():
        if f.is_symlink():                       # don't follow a symlinked secret file
            raise RuntimeError(f"{f} is a symlink (refused)")
        info = f.stat()
        if info.st_mode & 0o077:                 # group/other accessible -> refuse (fail closed)
            raise RuntimeError(f"{f} permissions too open (must be 0600)")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RuntimeError(f"{f} not owned by the current user (refused)")
        for line in f.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == env_name and v.strip():
                return SecretStr(v.strip())
    return None


def gateway_parse(schema: Type[T], system: str, user: str, *, stage: str,
                  spec: ProviderSpec, content_class: str = "abstract",
                  max_tokens: int = 16000) -> T:
    """Route a schema-constrained parse to the spec's provider/model. Fails closed on
    an unsupported source, a missing API key, or a content class the provider's data
    policy forbids (e.g. paywalled full text to a cloud provider). Records the ACTUAL
    provider+model. ``content_class`` ∈ {metadata, abstract, fulltext, manuscript}."""
    if spec.source not in _SUPPORTED:
        raise ValueError(
            f"unsupported provider source {spec.source!r} (supported: {sorted(_SUPPORTED)}; "
            f"OpenAI/Gemini plug into the gateway seam)")
    if not spec.model:
        # An empty model would silently fall back to the client default while the stage
        # log records "" — a provenance lie. Require an explicit model.
        raise ValueError("ProviderSpec.model must be non-empty")

    # Data-governance gate BEFORE any egress: don't send a content class the provider's
    # policy forbids (cloud providers reject fulltext/manuscript by default).
    from core.sr.orchestrator.security import assert_egress_allowed
    assert_egress_allowed(spec.source, content_class)

    if spec.source == "local":
        from core.sr.orchestrator.security import validate_endpoint
        endpoint = spec.endpoint or client.OLLAMA_HOST
        validate_endpoint(endpoint, allow_remote=os.environ.get("ONCO_ALLOW_REMOTE_ENDPOINT") == "1")
        # pass the SAME endpoint that was validated (not spec.endpoint-or-None) so the
        # validated host and the connected host can't desync.
        obj = client._ollama_parse(schema, system, user, max_tokens=max_tokens,
                                   model=spec.model, host=endpoint)
        client._record_stage(stage, spec.model, "ollama")
        return obj
    # anthropic — native structured-output path (adaptive thinking/effort) preserved.
    key = load_key(spec.source)
    if key is None:
        raise MissingKeyError(f"{spec.source}: {_ENV_KEY.get(spec.source)} not configured")
    obj = client._anthropic_parse(schema, system, user, max_tokens, model=spec.model,
                                  api_key=key.reveal())          # wire the resolved key to egress
    client._record_stage(stage, spec.model, "anthropic")
    return obj
