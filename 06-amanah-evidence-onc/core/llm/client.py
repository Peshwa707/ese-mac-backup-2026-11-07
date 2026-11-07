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
# ``parse``; read by ai_reporting + provenance so the AI-use statement reflects the
# models ACTUALLY used at runtime (and, once the router lands, local vs cloud).
_STAGE_LOG: ContextVar[list[dict] | None] = ContextVar("stage_log", default=None)


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
    for e in log:
        if e["stage"] == stage and e["model"] == model and e["backend"] == backend:
            e["n_calls"] += 1
            return
    log.append({"stage": stage, "model": model, "backend": backend, "n_calls": 1})


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
from core.paths import env  # noqa: E402

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
            _record_stage(stage, MODEL, "anthropic (local-fallback)")
            return _anthropic_parse(schema, system, user, max_tokens)
    _record_stage(stage, MODEL, "anthropic")
    return _anthropic_parse(schema, system, user, max_tokens)


# ---------------------------------------------------------------------------
# Message Batches API — ~50% cheaper, higher throughput for large screening runs.
# Opt-in: callers pass use_batch; the sync `parse` path stays the default and is the
# correctness fallback. A request whose batch result errors or yields no parseable
# output is OMITTED from the returned map so the caller fails closed (screening marks
# the missing record UNCERTAIN — never a silent loss).
# ---------------------------------------------------------------------------
BATCH_MIN_REQUESTS = int(env("SR_BATCH_MIN", default="20"))
BATCH_POLL_SECONDS = float(env("SR_BATCH_POLL_SECONDS", default="3"))
BATCH_TIMEOUT_SECONDS = float(env("SR_BATCH_TIMEOUT_SECONDS", default="3600"))


def _parse_batch_sync(schema: Type[T], requests: list[tuple[str, str, str]], *,
                      max_tokens: int, stage: str) -> dict[str, T]:
    """Fallback: run each request through the proven sync ``parse``; drop failures."""
    out: dict[str, T] = {}
    for custom_id, system, user in requests:
        try:
            out[custom_id] = parse(schema, system, user, max_tokens=max_tokens, stage=stage)
        except RuntimeError:
            pass
    return out


def _extract_parsed(schema: Type[T], message) -> T | None:
    """Pull a schema instance out of a batch result Message — prefer the SDK's
    parsed_output (present when output_format was set), else validate the text JSON."""
    obj = getattr(message, "parsed_output", None)
    if obj is not None:
        return obj
    parts = getattr(message, "content", None) or []
    text = "".join(getattr(p, "text", "") for p in parts) if not isinstance(parts, str) else parts
    if not text:
        return None
    try:
        return schema.model_validate_json(text)
    except Exception:
        return None


def _structured_output_param(schema: Type[T]) -> dict:
    """Serialized Structured-Outputs request param (pydantic class -> JSON-schema dict).

    ``messages.parse(output_format=Schema)`` accepts the class and serializes it
    internally, but raw ``messages.batches.create`` params do NOT get that helper
    transformation — so the batch path must send the schema in its wire form. (Exact
    envelope keys to be confirmed against the live SDK on the first real batched run;
    if they differ the batch submit fails and parse_batch falls back to sync, so
    correctness is unaffected — only the cost win waits on that confirmation.)"""
    return {"type": "json_schema", "name": schema.__name__, "schema": schema.model_json_schema()}


def _anthropic_parse_batch(schema: Type[T], requests: list[tuple[str, str, str]], *,
                           max_tokens: int, stage: str) -> dict[str, T]:
    import time

    cli = _get_client()
    batch = cli.messages.batches.create(requests=[
        {"custom_id": cid, "params": {
            "model": MODEL,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            # Raw batches.create gets no parse() helper, so the structured-output schema
            # rides under output_config.format (not a sibling output_format field).
            "output_config": {"effort": "high", "format": _structured_output_param(schema)},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }}
        for cid, system, user in requests
    ])
    waited = 0.0
    status = getattr(batch, "processing_status", "ended")
    while status != "ended":
        if waited >= BATCH_TIMEOUT_SECONDS:
            raise RuntimeError(f"batch {getattr(batch, 'id', '?')} did not finish in {BATCH_TIMEOUT_SECONDS}s")
        time.sleep(BATCH_POLL_SECONDS)
        waited += BATCH_POLL_SECONDS
        status = getattr(cli.messages.batches.retrieve(batch.id), "processing_status", "ended")

    out: dict[str, T] = {}
    for res in cli.messages.batches.results(batch.id):
        result = getattr(res, "result", None)
        if getattr(result, "type", None) != "succeeded":
            continue                     # errored/expired -> omit; caller fails closed
        obj = _extract_parsed(schema, getattr(result, "message", None))
        if obj is not None:
            out[res.custom_id] = obj
    _record_stage(stage, MODEL, "anthropic (batch)")
    return out


def parse_batch(schema: Type[T], requests, *, max_tokens: int = 16000,
                stage: str = "unknown") -> dict[str, T]:
    """Structured output for many prompts via the Anthropic Message Batches API.

    ``requests`` is an iterable of ``(custom_id, system, user)``. Returns
    ``{custom_id: schema instance}`` for every request that produced valid output; ids
    that errored or returned nothing are OMITTED (fail closed). Uses the sync path for
    small batches, and falls back to sync if the batches API is unavailable or errors —
    correctness is always preserved, the batch is only a cost/throughput lever."""
    reqs = list(requests)
    if not reqs:
        return {}
    if len(reqs) < BATCH_MIN_REQUESTS:
        return _parse_batch_sync(schema, reqs, max_tokens=max_tokens, stage=stage)
    try:
        return _anthropic_parse_batch(schema, reqs, max_tokens=max_tokens, stage=stage)
    except Exception:
        # Batch namespace missing or submission failed — never lose work; the sync path
        # is the source of truth. Recorded distinctly so provenance shows the fallback.
        _record_stage(stage, MODEL, "anthropic (batch-fallback)")
        return _parse_batch_sync(schema, reqs, max_tokens=max_tokens, stage=stage)


def _client_with_key(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _anthropic_parse(schema: Type[T], system: str, user: str, max_tokens: int,
                     model: str | None = None, api_key: str | None = None) -> T:
    cli = _client_with_key(api_key) if api_key else _get_client()
    resp = cli.messages.parse(
        model=model or MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    if resp.parsed_output is None:
        raise RuntimeError("LLM returned no parseable structured output")
    return resp.parsed_output


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
        validate_endpoint(host, allow_remote=env("ONCO_ALLOW_REMOTE_ENDPOINT", default="") == "1")
        resp = httpx.post(f"{host}/api/chat", json=payload, timeout=600, follow_redirects=False)
        resp.raise_for_status()
        content = (resp.json().get("message") or {}).get("content") or ""
    except Exception as exc:  # transport / HTTP error
        raise RuntimeError(f"local LLM ({model}) call failed: {exc}") from exc
    try:
        # Local models often wrap valid JSON in ```json fences or a prose preamble;
        # extract the first complete JSON value, then validate against the schema.
        return schema.model_validate(_extract_json(content))
    except Exception as exc:  # schema-violating output -> fail closed
        raise RuntimeError(
            f"local LLM ({model}) returned no schema-valid structured output") from exc


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
