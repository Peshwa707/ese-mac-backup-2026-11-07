"""AI reviewer roster resolution + independence preflight (WP-M2.2, spec §4/§7a).

The two AI reviewers must be genuinely independent models — otherwise the dual-AI
cross-check claim (and any agreement metric) is meaningless. This module resolves
each reviewer to a ProviderSpec from the vertical's roster and fails closed when
the two AI reviewers are not independent. It does not represent human Reviewer 2.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from core.llm import client
from core.llm.gateway import ProviderSpec

_LOOPBACK = {"localhost", "::1", "0.0.0.0"}
_DEFAULT_PORT = {"http": 80, "https": 443}


def _canonical_endpoint(url: str) -> str:
    """Normalize an endpoint so equivalent spellings compare equal (trailing slash,
    scheme/host case, default ports, loopback aliases)."""
    if not url:
        return ""
    p = urlsplit(url if "://" in url else "http://" + url)
    scheme = (p.scheme or "http").lower()
    host = (p.hostname or "").lower()
    if host in _LOOPBACK:
        host = "127.0.0.1"
    port = p.port
    netloc = host if (port is None or port == _DEFAULT_PORT.get(scheme)) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, p.path.rstrip("/"), "", ""))

# Default roster: R1 = cloud Anthropic, R2 = local model (different family) -> independent
# and key-free for R2. Override per-vertical via pico `reviewers:`.
_DEFAULTS: dict[str, dict] = {
    "r1": {"source": "anthropic", "model": "claude-opus-4-8"},
    "r2": {"source": "local", "model": client.OLLAMA_MODEL, "endpoint": client.OLLAMA_HOST},
    "adjudicator": {"source": "anthropic", "model": "claude-opus-4-8"},
}

_FAMILIES = ("claude", "gpt", "gemini", "llama", "mistral", "qwen", "gemma", "phi", "deepseek")
# Single-family API providers: the source determines the family regardless of the model
# alias (so "opus-4-8"/"sonnet-4-6" without the "claude" token still count as one family).
_SOURCE_FAMILY = {"anthropic": "claude", "openai": "gpt", "gemini": "gemini"}


class ReviewerIndependenceError(RuntimeError):
    """Two reviewers are not independent (same identity or same model family)."""


def resolve_reviewer(cfg, name: str) -> ProviderSpec:
    """Resolve a reviewer name to a ProviderSpec from cfg.reviewers, falling back to
    the documented defaults. Fails closed on a roster entry missing source/model."""
    roster = getattr(cfg, "reviewers", {}) or {}
    spec = dict(_DEFAULTS.get(name, {}))
    spec.update(roster.get(name, {}) or {})
    if not spec.get("source") or not spec.get("model"):
        raise ValueError(f"reviewer {name!r} roster entry missing source/model")
    return ProviderSpec(source=spec["source"], model=spec["model"],
                        endpoint=spec.get("endpoint", "") or "")


def model_family(spec: ProviderSpec) -> str:
    """Coarse model family for independence checking (claude/gpt/gemini/llama/...).

    A single-family API source pins the family (so provider aliases that omit the
    family token can't masquerade as a different family)."""
    if spec.source in _SOURCE_FAMILY:
        return _SOURCE_FAMILY[spec.source]
    m = spec.model.lower()
    for fam in _FAMILIES:
        if fam in m:
            return fam
    return f"{spec.source}:{m.split(':')[0]}"     # fallback: source + base model name


def assert_independent(r1: ProviderSpec, r2: ProviderSpec) -> None:
    """Fail closed unless R1 and R2 are independent: not the same identity, not the same
    local endpoint, and not the same model family."""
    ep1, ep2 = _canonical_endpoint(r1.endpoint), _canonical_endpoint(r2.endpoint)
    if r1.source == r2.source and r1.model == r2.model and ep1 == ep2:
        raise ReviewerIndependenceError(
            f"AI Reviewer 1 and secondary AI reviewer are identical ({r1.source}/{r1.model}); "
            f"configure two different models")
    if r1.source == "local" and r2.source == "local" and ep1 and ep1 == ep2:
        raise ReviewerIndependenceError(
            f"AI reviewers share the same local endpoint {r1.endpoint!r}; "
            f"co-located models are not independent")
    if model_family(r1) == model_family(r2):
        raise ReviewerIndependenceError(
            f"AI reviewers share model family {model_family(r1)!r} "
            f"({r1.model} vs {r2.model}); independent reviewers must differ in family")
