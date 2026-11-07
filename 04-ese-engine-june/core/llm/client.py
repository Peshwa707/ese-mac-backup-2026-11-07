"""Thin Anthropic wrapper used by every LLM-backed pipeline stage.

Centralizes model selection and request defaults so the rest of the engine
never touches the SDK directly. Every stage — including the long CARE manuscript
— uses ``parse`` (schema-constrained structured output). ``complete_stream`` is a
free-text streaming helper provided for callers that need to stream a large
plain-text completion to avoid HTTP timeouts; it is NOT currently wired into the
pipeline (the manuscript is a structured ``parse`` call).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Type, TypeVar

from pydantic import BaseModel

# Most capable model; adaptive thinking is the only on-mode. Keep the id here
# only — it must not leak into committed reports, prompts, or PR artifacts.
MODEL = "claude-opus-4-8"

T = TypeVar("T", bound=BaseModel)

_client = None

# Per-run record of which model/backend served which pipeline stage. Populated by
# ``parse``; persisted into the provenance record (stage_models) so the AI-use statement
# reflects the models ACTUALLY used at runtime (and, once the router lands, local vs cloud).
_STAGE_LOG: ContextVar[list[dict] | None] = ContextVar("stage_log", default=None)

# Per-call SERVED model id + fingerprint (the model that ACTUALLY answered — may differ
# from the requested id via provider aliases/routing). Set by each _*_parse on a
# successful return, consumed by _record_stage. Reproducibility req: requested AND served.
_LAST_SERVED: ContextVar[dict | None] = ContextVar("last_served", default=None)


def _set_served(served_model: str | None, fingerprint: str | None = None) -> None:
    _LAST_SERVED.set({"served_model": served_model, "fingerprint": fingerprint})


def _merge_served(entry: dict, served: dict | None) -> None:
    """Additively record served model/fingerprint on a stage entry. Existing keys
    (stage/model/backend/n_calls) are untouched so stage-log consumers don't break."""
    if not served or served.get("served_model") is None:
        return
    lst = entry.setdefault("served", [])
    if served not in lst:
        lst.append(served)


def _stage_log_list() -> list[dict]:
    log = _STAGE_LOG.get()
    if log is None:
        log = []
        _STAGE_LOG.set(log)
    return log


def current_model() -> str:
    """The model id this client currently routes to (single-backend for now)."""
    return MODEL


def reset_stage_log() -> None:
    """Clear the per-run stage log (call at the start of a run)."""
    _STAGE_LOG.set([])


def stage_log() -> list[dict]:
    """Aggregated stage→model/backend usage for this run."""
    return [dict(e) for e in _stage_log_list()]


def _record_stage(stage: str, model: str, backend: str) -> None:
    log = _stage_log_list()
    served = _LAST_SERVED.get()
    _LAST_SERVED.set(None)               # consume so it can't leak into the next call
    for e in log:
        if e["stage"] == stage and e["model"] == model and e["backend"] == backend:
            e["n_calls"] += 1
            _merge_served(e, served)
            return
    entry = {"stage": stage, "model": model, "backend": backend, "n_calls": 1}
    _merge_served(entry, served)
    log.append(entry)


def _get_client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so the package loads without a key

        _client = anthropic.Anthropic()
    return _client


# ---------------------------------------------------------------------------
# Tiered routing. The cost surface is one decision: which backend serves which
# stage. High-volume stages (screening, extraction) move to a local model in the
# hybrid/local profiles; low-volume high-stakes stages stay on the cloud model.
# claim_check is the adversarial safety net and NEVER goes local.
# ---------------------------------------------------------------------------
from core.paths import allow_remote_endpoint, env  # noqa: E402

OLLAMA_HOST = env("OLLAMA_HOST", default="http://localhost:11434")
# Local model preference (only used when posture routes a stage local — NOT the
# default; posture is `cloud` until a tool's local performance is benchmark-validated).
# Benchmark (CR-extraction, 43 known includes): gemma4 read all 43 cleanly (0 failures)
# vs qwen2.5:7b (14 failures); qwen3.5:9b REJECTED (ignores Ollama `format`). NEITHER
# reproduced the CR number yet, so extraction stays cloud. Override with OLLAMA_MODEL.
OLLAMA_MODEL = env("OLLAMA_MODEL", default="gemma4:latest")

_LOCAL_STAGES = ("screening", "extraction", "structure", "synthesis", "manuscript", "conflict")
_ROUTING: dict[str, dict[str, str]] = {
    "cloud": {},                                                  # everything anthropic
    "hybrid": {"screening": "ollama", "extraction": "ollama"},    # high-volume local
    "local": {s: "ollama" for s in _LOCAL_STAGES},                # all but claim_check local
}


def active_profile() -> str:
    p = env("SR_LLM_PROFILE", default="cloud").lower()
    return p if p in _ROUTING else "cloud"


# A backend pinned by the tool runner for the duration of one tool call. Cloud-only
# stages ignore it, so the claim-check safety net can never be forced local even here.
_FORCED_BACKEND: ContextVar[str | None] = ContextVar("forced_backend", default=None)
_CLOUD_ONLY_STAGES = {"claim_check"}


def set_forced_backend(backend: str) -> None:
    """Pin the backend for the next call(s). 'cloud'->anthropic, 'local'->ollama."""
    _FORCED_BACKEND.set({"cloud": "anthropic", "local": "ollama"}.get(backend, backend))


def clear_forced_backend() -> None:
    _FORCED_BACKEND.set(None)


def current_forced_backend() -> str | None:
    return _FORCED_BACKEND.get()


def backend_for(stage: str) -> str:
    """Which backend serves this stage ('anthropic'|'ollama'): a forced pin wins,
    except cloud-only stages; otherwise the active SR_LLM_PROFILE routing applies."""
    forced = current_forced_backend()
    if forced and stage not in _CLOUD_ONLY_STAGES:
        return forced
    return _ROUTING[active_profile()].get(stage, "anthropic")


def parse(schema: Type[T], system: str, user: str, *, max_tokens: int = 16000,
          stage: str = "unknown") -> T:
    """Structured extraction / grading / claim-check against a Pydantic schema.

    ``stage`` tags the call for the AI-use provenance record AND selects the backend
    via the active SR_LLM_PROFILE. The contract is identical for every backend: a
    schema-validated Pydantic instance out, or a hard ``RuntimeError`` (never a
    silently malformed result).
    """
    if backend_for(stage) == "ollama":
        try:
            obj = _ollama_parse(schema, system, user, max_tokens=max_tokens)
            _record_stage(stage, OLLAMA_MODEL, "ollama")
            return obj
        except RuntimeError:
            # Local model failed to produce schema-valid output — fall back to the
            # cloud model for correctness (recorded distinctly so the AI-use statement
            # shows where the local path could not be trusted).
            obj = _anthropic_parse(schema, system, user, max_tokens)
            _record_stage(stage, MODEL, "anthropic (local-fallback)")  # AFTER parse so served is set
            return obj
    obj = _anthropic_parse(schema, system, user, max_tokens)
    _record_stage(stage, MODEL, "anthropic")                           # AFTER parse so served is set
    return obj


def _client_with_key(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _anthropic_parse(schema: Type[T], system: str, user: str, max_tokens: int,
                     model: str | None = None, api_key: str | None = None) -> T:
    cli = _client_with_key(api_key) if api_key else _get_client()
    try:
        resp = cli.messages.parse(
            model=model or MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
    except Exception as exc:
        raise RuntimeError(f"anthropic ({model or MODEL}) call failed: {type(exc).__name__}") from exc
    if resp.parsed_output is None:
        raise RuntimeError("LLM returned no parseable structured output")
    _set_served(getattr(resp, "model", None), getattr(resp, "system_fingerprint", None))
    return resp.parsed_output


def _openai_parse(schema: Type[T], system: str, user: str, max_tokens: int, *,
                  model: str, api_key: str, base_url: str | None = None) -> T:
    """Structured output via OpenAI's Responses API.

    Uses schema-constrained parsing into the provided Pydantic type and returns a
    validated instance, or fails closed with ``RuntimeError`` on any SDK/transport,
    refusal, or schema-output failure. The downstream contract matches the other
    providers so cite-or-omit and quote-anchoring still protect against a
    different-family reviewer.
    """
    import openai

    def _item_field(obj, name: str):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def _has_refusal(output) -> bool:
        for item in output or []:
            if _item_field(item, "type") == "refusal":
                return True
            for content in _item_field(item, "content") or []:
                if _item_field(content, "type") == "refusal":
                    return True
        return False

    cli = openai.OpenAI(api_key=api_key, base_url=base_url or None)
    try:
        resp = cli.responses.parse(
            model=model,
            instructions=system,
            input=user,
            text_format=schema,
            max_output_tokens=max_tokens,
        )
    except Exception as exc:
        raise RuntimeError(f"openai ({model}) call failed: {type(exc).__name__}") from exc
    if resp.output_parsed is None or _has_refusal(getattr(resp, "output", None)):
        raise RuntimeError(f"openai ({model}) returned no schema-valid structured output")
    _set_served(getattr(resp, "model", None), getattr(resp, "system_fingerprint", None))
    return resp.output_parsed


def _ollama_parse(schema: Type[T], system: str, user: str, *, max_tokens: int = 16000,
                  model: str | None = None, host: str | None = None) -> T:
    """Local structured output via Ollama's JSON-schema-constrained decoding.

    Sends the Pydantic model's JSON schema as Ollama's ``format`` so the model is
    constrained to emit conforming JSON, then validates back into the Pydantic type.
    Fails closed (RuntimeError) on any transport/parse/validation failure — the same
    contract as the cloud path, so cite-or-omit and quote-anchoring downstream still
    protect against a weaker local model. ``model``/``host`` override the defaults so a
    reviewer roster can point at a specific local model/endpoint.
    """
    import httpx

    model = model or OLLAMA_MODEL
    host = host or OLLAMA_HOST
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "think": False,                       # qwen3.x thinking off (islamic-kb gotcha)
        "format": schema.model_json_schema(),
        "options": {"temperature": 0, "num_predict": max_tokens},
    }
    try:
        # SSRF guard at the connect point (covers the legacy/direct path that bypasses the
        # gateway) + no redirect-following so a 3xx can't bounce to an internal address.
        from core.sr.orchestrator.security import validate_endpoint
        validate_endpoint(host, allow_remote=allow_remote_endpoint())
        resp = httpx.post(f"{host}/api/chat", json=payload, timeout=600, follow_redirects=False)
        resp.raise_for_status()
        content = (resp.json().get("message") or {}).get("content") or ""
    except Exception as exc:  # transport / HTTP error
        raise RuntimeError(f"local LLM ({model}) call failed: {exc}") from exc
    try:
        # Local models often wrap valid JSON in ```json fences or a prose preamble;
        # extract the first complete JSON value, then validate against the schema.
        obj = schema.model_validate(_extract_json(content))
    except Exception as exc:  # schema-violating output -> fail closed
        raise RuntimeError(
            f"local LLM ({model}) returned no schema-valid structured output") from exc
    _set_served(model, None)   # local: served model = the local model id (no fingerprint)
    return obj


def _extract_json(text: str):
    """Return the first complete JSON object/array embedded in ``text`` (tolerates
    markdown ``` fences, a prose preamble, and trailing characters). Raises if none."""
    import json

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in local-model output")


def complete_stream(system: str, user: str, *, max_tokens: int = 64000) -> str:
    """Free-text completion (streamed). Available for large plain-text outputs;
    not currently used by the pipeline (the manuscript uses ``parse``)."""
    with _get_client().messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    return "".join(b.text for b in msg.content if b.type == "text")
