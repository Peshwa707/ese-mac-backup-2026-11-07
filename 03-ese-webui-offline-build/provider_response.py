"""Provider-specific health-response normalization — pure and offline.

This module turns a *bounded, synthetic* provider health response into a
canonical redacted summary. It is a shape contract for the future live
adapter, exercised today only with synthetic fixtures — no code path here
performs I/O, resolves credentials, or reads environment values, keychains,
cookies, browser profiles, or subscription sessions.

- **Closed input grammar per provider.** Each of the seven provider modes
  accepts exactly one validated response family: an OpenAI-compatible model
  list (``openai_api``, ``moonshot_kimi_api``, ``deepseek_api``), the
  Anthropic model list, the Ollama tag list (``ollama_local``,
  ``ollama_cloud``), a provider-matched auth-error body on 401/403 for
  ``api_key`` modes, or — for ``interactive_subscription`` — a *manual
  operator record*, never an HTTP response. Anything else (unknown keys,
  wrong types, out-of-bounds sizes, cross-provider shapes, unexpected
  statuses) is rejected with a typed error.
- **The raw response never escapes.** The summary carries the provider,
  operation, status/classification, a safe count (or the manual result),
  and a canonical sha256 of the screened response — never model ids, error
  messages, notes, or any other body content. ``public_view`` screens the
  summary on the way out.
- **Secret-shaped input is rejected, not redacted.** A response echoing
  secret material (the classic case: an auth error quoting the presented
  key) refuses to normalize at all, and the secret is not echoed in the
  exception text.
- **Subscription normalization stays manual.** An automated HTTP-shaped
  response (``status``/``body``/``headers`` keys) for a subscription mode
  raises :class:`AutomatedSubscriptionResponseError` — the WebUI never
  automates a user-owned ChatGPT/Claude session, so no automated response
  for one can exist honestly.
- **Deterministic.** No clocks, no randomness; equal inputs yield
  byte-identical summaries via ``provider_health.to_json``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from provider_health import to_json
from providers import (
    ACCESS_API_KEY,
    ACCESS_INTERACTIVE_SUBSCRIPTION,
    ProviderConfigError,
    public_view,
    validate_provider_config,
)

OPERATION = "health_check"

_OK_STATUS = 200
_AUTH_REJECTED_STATUSES = frozenset({401, 403})

# Bounds for "bounded synthetic": anything larger is out of contract.
_MAX_ITEMS = 512
_MAX_STRING = 256
_MAX_DEPTH = 6
_MAX_KEYS = 64

# Keys that mark an automated HTTP response. Their presence in a
# subscription-mode input is a category error, not a shape error.
_AUTOMATED_MARKER_KEYS = frozenset({"status", "body", "headers", "http", "response"})

_MANUAL_RESULTS = ("ok", "failed")
_OPENAI_COMPATIBLE = frozenset({"openai_api", "moonshot_kimi_api", "deepseek_api"})
_OLLAMA = frozenset({"ollama_local", "ollama_cloud"})


class ResponseValidationError(ProviderConfigError):
    """Raised for a malformed, out-of-bounds, or mis-matched response."""


class AutomatedSubscriptionResponseError(ResponseValidationError):
    """Raised when an automated HTTP response is offered for a manual mode."""


def _screen(value: Any) -> None:
    """Reject secret-shaped material anywhere in the input, without echo."""
    try:
        public_view({"response": value})
    except ProviderConfigError:
        raise ResponseValidationError(
            "response carries secret-shaped material; refusing to normalize "
            "or echo it"
        ) from None


def _check_bounds(value: Any, depth: int = 0) -> None:
    """Enforce the bounded-synthetic contract structurally."""
    if depth > _MAX_DEPTH:
        raise ResponseValidationError("response nests deeper than the bounded contract")
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            raise ResponseValidationError(
                "response contains a string longer than the bounded contract"
            )
        return
    if isinstance(value, list):
        if len(value) > _MAX_ITEMS:
            raise ResponseValidationError(
                "response contains a list larger than the bounded contract"
            )
        for item in value:
            _check_bounds(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_KEYS:
            raise ResponseValidationError(
                "response contains an object wider than the bounded contract"
            )
        for key, sub in value.items():
            _check_bounds(key, depth + 1)
            _check_bounds(sub, depth + 1)
        return
    # Closed scalar set: anything that is not plain JSON data (bytes, sets,
    # NaN/Inf, arbitrary objects) is outside the bounded contract — reject
    # here so no later step can raise an untyped serialization error.
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and value == value and value not in (
        float("inf"),
        float("-inf"),
    ):
        return
    raise ResponseValidationError(
        "response contains a value outside the bounded JSON contract"
    )


def _require_keys(
    obj: dict[str, Any], required: frozenset, optional: frozenset, where: str
) -> None:
    keys = set(obj)
    if not required.issubset(keys) or keys - required - optional:
        raise ResponseValidationError(f"{where} has unexpected or missing fields")


def _require_str(obj: dict[str, Any], key: str, where: str) -> None:
    if key in obj and not isinstance(obj[key], str):
        raise ResponseValidationError(f"{where}.{key} must be a string")


def _validate_openai_list(body: dict[str, Any]) -> int:
    _require_keys(body, frozenset({"object", "data"}), frozenset(), "model list")
    if body["object"] != "list" or not isinstance(body["data"], list):
        raise ResponseValidationError(
            "model list must be {object: 'list', data: [...]}"
        )
    for entry in body["data"]:
        if not isinstance(entry, dict):
            raise ResponseValidationError("model entries must be objects")
        _require_keys(
            entry,
            frozenset({"id"}),
            frozenset({"object", "created", "owned_by"}),
            "model entry",
        )
        if not isinstance(entry["id"], str):
            raise ResponseValidationError("model entry.id must be a string")
        _require_str(entry, "object", "model entry")
        _require_str(entry, "owned_by", "model entry")
        if "created" in entry and not isinstance(entry["created"], int):
            raise ResponseValidationError("model entry.created must be an integer")
    return len(body["data"])


def _validate_anthropic_list(body: dict[str, Any]) -> int:
    _require_keys(
        body,
        frozenset({"data"}),
        frozenset({"has_more", "first_id", "last_id"}),
        "model list",
    )
    if not isinstance(body["data"], list):
        raise ResponseValidationError("model list data must be a list")
    if "has_more" in body and not isinstance(body["has_more"], bool):
        raise ResponseValidationError("model list has_more must be a boolean")
    for key in ("first_id", "last_id"):
        if key in body and body[key] is not None and not isinstance(body[key], str):
            raise ResponseValidationError(f"model list {key} must be a string or null")
    for entry in body["data"]:
        if not isinstance(entry, dict):
            raise ResponseValidationError("model entries must be objects")
        _require_keys(
            entry,
            frozenset({"type", "id"}),
            frozenset({"display_name", "created_at"}),
            "model entry",
        )
        if entry["type"] != "model" or not isinstance(entry["id"], str):
            raise ResponseValidationError(
                "model entry must be {type: 'model', id: <string>, ...}"
            )
        _require_str(entry, "display_name", "model entry")
        _require_str(entry, "created_at", "model entry")
    return len(body["data"])


def _validate_ollama_list(body: dict[str, Any]) -> int:
    _require_keys(body, frozenset({"models"}), frozenset(), "tag list")
    if not isinstance(body["models"], list):
        raise ResponseValidationError("tag list models must be a list")
    for entry in body["models"]:
        if not isinstance(entry, dict):
            raise ResponseValidationError("tag entries must be objects")
        _require_keys(
            entry,
            frozenset(),
            frozenset({"name", "model", "modified_at", "size", "digest", "details"}),
            "tag entry",
        )
        named = False
        for key in ("name", "model"):
            if key in entry:
                if not isinstance(entry[key], str):
                    raise ResponseValidationError(f"tag entry.{key} must be a string")
                named = True
        if not named:
            raise ResponseValidationError("tag entry must name a model")
        _require_str(entry, "modified_at", "tag entry")
        _require_str(entry, "digest", "tag entry")
        if "size" in entry and not isinstance(entry["size"], int):
            raise ResponseValidationError("tag entry.size must be an integer")
        if "details" in entry and not isinstance(entry["details"], dict):
            raise ResponseValidationError("tag entry.details must be an object")
    return len(body["models"])


def _validate_ok_body(provider_id: str, body: dict[str, Any]) -> int:
    if provider_id in _OPENAI_COMPATIBLE:
        return _validate_openai_list(body)
    if provider_id == "anthropic_api":
        return _validate_anthropic_list(body)
    if provider_id in _OLLAMA:
        return _validate_ollama_list(body)
    raise ResponseValidationError("no validated response shape for this provider")


def _validate_auth_error_body(provider_id: str, body: dict[str, Any]) -> None:
    if provider_id in _OPENAI_COMPATIBLE:
        _require_keys(body, frozenset({"error"}), frozenset(), "auth error")
        error = body["error"]
        if not isinstance(error, dict):
            raise ResponseValidationError("auth error.error must be an object")
        _require_keys(
            error,
            frozenset({"message"}),
            frozenset({"type", "param", "code"}),
            "auth error",
        )
        if not isinstance(error["message"], str):
            raise ResponseValidationError("auth error message must be a string")
        _require_str(error, "type", "auth error")
        for key in ("param", "code"):
            if key in error and error[key] is not None and not isinstance(error[key], str):
                raise ResponseValidationError(f"auth error {key} must be a string or null")
        return
    if provider_id == "anthropic_api":
        _require_keys(body, frozenset({"type", "error"}), frozenset(), "auth error")
        error = body["error"]
        if (
            body["type"] != "error"
            or not isinstance(error, dict)
            or set(error) != {"type", "message"}
            or not isinstance(error["type"], str)
            or not isinstance(error["message"], str)
        ):
            raise ResponseValidationError(
                "auth error must be {type: 'error', error: {type, message}}"
            )
        return
    if provider_id == "ollama_cloud":
        _require_keys(body, frozenset({"error"}), frozenset(), "auth error")
        if not isinstance(body["error"], str):
            raise ResponseValidationError("auth error must be {error: <string>}")
        return
    raise ResponseValidationError(
        "this provider mode has no validated auth-error shape"
    )


def _normalize_manual(validated: dict[str, Any], record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        automated = _AUTOMATED_MARKER_KEYS.intersection(record)
        if automated:
            raise AutomatedSubscriptionResponseError(
                "an automated HTTP response was offered for a user-owned "
                "subscription session; subscription health results are "
                "manual operator records only — the WebUI never automates "
                "these sessions"
            )
    if not isinstance(record, dict):
        raise ResponseValidationError(
            "subscription health results must be a manual operator record"
        )
    _require_keys(
        record,
        frozenset({"mode", "service", "operator_result"}),
        frozenset({"note"}),
        "manual record",
    )
    if record["mode"] != "manual":
        raise ResponseValidationError(
            "manual record mode must be the literal 'manual'"
        )
    if record["service"] != validated["service"]:
        raise ResponseValidationError(
            "manual record names a different service than the configuration"
        )
    if record["operator_result"] not in _MANUAL_RESULTS:
        raise ResponseValidationError(
            "manual record operator_result must be 'ok' or 'failed'"
        )
    if "note" in record and not isinstance(record["note"], str):
        raise ResponseValidationError("manual record note must be a string")
    result = record["operator_result"]
    return {
        "provider": validated["provider"],
        "access": validated["access"],
        "operation": OPERATION,
        "mode": "manual_operator",
        "service": validated["service"],
        "classification": "manual_ok" if result == "ok" else "manual_failed",
        "manual_result": result,
        "response_sha256": hashlib.sha256(
            to_json(dict(record)).encode("utf-8")
        ).hexdigest(),
    }


def _normalize_api(validated: dict[str, Any], response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ResponseValidationError(
            "API health responses must be {status: <int>, body: <object>}"
        )
    _require_keys(response, frozenset({"status", "body"}), frozenset(), "response")
    status, body = response["status"], response["body"]
    if isinstance(status, bool) or not isinstance(status, int):
        raise ResponseValidationError("response status must be an integer")
    if not isinstance(body, dict):
        raise ResponseValidationError("response body must be an object")

    provider_id = validated["provider"]
    if status == _OK_STATUS:
        classification = "ok"
        model_count: int | None = _validate_ok_body(provider_id, body)
    elif status in _AUTH_REJECTED_STATUSES and validated["access"] == ACCESS_API_KEY:
        _validate_auth_error_body(provider_id, body)
        classification = "auth_rejected"
        model_count = None
    else:
        raise ResponseValidationError(
            "response status is outside the bounded health-check contract "
            "(200, or 401/403 for api_key modes)"
        )
    summary: dict[str, Any] = {
        "provider": provider_id,
        "access": validated["access"],
        "operation": OPERATION,
        "mode": "api_http",
        "status": status,
        "classification": classification,
        "response_sha256": hashlib.sha256(
            to_json(dict(body)).encode("utf-8")
        ).hexdigest(),
    }
    if model_count is not None:
        summary["model_count"] = model_count
    return summary


def normalize_health_response(config: Any, response: Any) -> dict[str, Any]:
    """Normalize one bounded synthetic health response to a redacted summary.

    Pure and deterministic; fails closed, in order: configuration
    validation → secret screen (no echo) → bounds → mode-specific shape
    validation. The summary never includes the raw response — only the
    provider, operation, status/classification, a safe count or the manual
    result, and the canonical sha256 of the screened response.
    """
    validated = validate_provider_config(config)
    _screen(response)
    _check_bounds(response)
    if validated["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION:
        return public_view(_normalize_manual(validated, response))
    return public_view(_normalize_api(validated, response))
