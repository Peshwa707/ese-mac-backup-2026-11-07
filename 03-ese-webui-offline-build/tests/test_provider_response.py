"""Offline, deterministic tests for provider-specific health-response
normalization.

Every response here is bounded and synthetic — no network was or can be
involved. ``monkeypatch.setenv`` appears only to PROVE nothing reads
environment values.
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from provider_health import to_json
from provider_response import (
    AutomatedSubscriptionResponseError,
    ResponseValidationError,
    normalize_health_response,
)
from providers import ProviderConfigError

RESPONSE_SOURCE = Path(__file__).resolve().parent.parent / "provider_response.py"

OPENAI_CONFIG = {
    "provider": "openai_api",
    "credential_ref": {"kind": "env", "name": "OPENAI_API_KEY"},
}

OPENAI_BODY = {
    "object": "list",
    "data": [
        {"id": "gpt-4o", "object": "model", "created": 1687882411, "owned_by": "openai"},
        {"id": "gpt-4o-mini", "object": "model", "created": 1715367049, "owned_by": "system"},
    ],
}

ANTHROPIC_BODY = {
    "data": [
        {
            "type": "model",
            "id": "claude-sonnet-5",
            "display_name": "Claude Sonnet 5",
            "created_at": "2026-02-19T00:00:00Z",
        }
    ],
    "has_more": False,
    "first_id": "claude-sonnet-5",
    "last_id": "claude-sonnet-5",
}

OLLAMA_BODY = {
    "models": [
        {
            "name": "llama3.2:latest",
            "model": "llama3.2:latest",
            "size": 2019393189,
            "digest": "a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72",
        }
    ]
}

MOONSHOT_BODY = {"object": "list", "data": [{"id": "kimi-k2.6", "object": "model"}]}
DEEPSEEK_BODY = {"object": "list", "data": [{"id": "deepseek-chat", "object": "model"}]}

MANUAL_OK = {
    "mode": "manual",
    "service": "chatgpt",
    "operator_result": "ok",
    "note": "opened my own session and received a normal reply",
}


def _subscription_config(service="chatgpt"):
    return {"provider": "interactive_subscription", "service": service}


# --- happy-path normalization for every provider shape -----------------------


def test_openai_model_list_normalizes_to_ok_with_count():
    summary = normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": OPENAI_BODY})
    assert summary["provider"] == "openai_api"
    assert summary["operation"] == "health_check"
    assert summary["mode"] == "api_http"
    assert summary["classification"] == "ok"
    assert summary["status"] == 200
    assert summary["model_count"] == 2


def test_anthropic_model_list_normalizes_to_ok():
    summary = normalize_health_response(
        {"provider": "anthropic_api"}, {"status": 200, "body": ANTHROPIC_BODY}
    )
    assert summary["provider"] == "anthropic_api"
    assert summary["classification"] == "ok"
    assert summary["model_count"] == 1


@pytest.mark.parametrize(
    "provider_id,body",
    [("moonshot_kimi_api", MOONSHOT_BODY), ("deepseek_api", DEEPSEEK_BODY)],
)
def test_openai_compatible_providers_normalize_to_ok(provider_id, body):
    summary = normalize_health_response(
        {"provider": provider_id}, {"status": 200, "body": body}
    )
    assert summary["provider"] == provider_id
    assert summary["classification"] == "ok"
    assert summary["model_count"] == 1


@pytest.mark.parametrize("provider_id", ["ollama_local", "ollama_cloud"])
def test_ollama_tags_normalize_to_ok(provider_id):
    summary = normalize_health_response(
        {"provider": provider_id}, {"status": 200, "body": OLLAMA_BODY}
    )
    assert summary["provider"] == provider_id
    assert summary["classification"] == "ok"
    assert summary["model_count"] == 1


# --- auth-rejected classification --------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_openai_auth_error_normalizes_to_auth_rejected(status):
    body = {
        "error": {
            "message": "Incorrect API key provided",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }
    summary = normalize_health_response(OPENAI_CONFIG, {"status": status, "body": body})
    assert summary["classification"] == "auth_rejected"
    assert summary["status"] == status
    assert "model_count" not in summary


def test_anthropic_auth_error_normalizes_to_auth_rejected():
    body = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
    }
    summary = normalize_health_response(
        {"provider": "anthropic_api"}, {"status": 401, "body": body}
    )
    assert summary["classification"] == "auth_rejected"


def test_ollama_cloud_auth_error_normalizes_to_auth_rejected():
    summary = normalize_health_response(
        {"provider": "ollama_cloud"}, {"status": 401, "body": {"error": "unauthorized"}}
    )
    assert summary["classification"] == "auth_rejected"


def test_ollama_local_no_auth_mode_accepts_only_200():
    with pytest.raises(ResponseValidationError):
        normalize_health_response(
            {"provider": "ollama_local"}, {"status": 401, "body": {"error": "unauthorized"}}
        )


@pytest.mark.parametrize("status", [0, 100, 204, 301, 404, 429, 500, -1])
def test_statuses_outside_the_bounded_set_are_rejected(status):
    with pytest.raises(ResponseValidationError):
        normalize_health_response(OPENAI_CONFIG, {"status": status, "body": OPENAI_BODY})


# --- subscription modes stay manual -------------------------------------------


@pytest.mark.parametrize("service", ["chatgpt", "claude"])
@pytest.mark.parametrize("result,classification", [("ok", "manual_ok"), ("failed", "manual_failed")])
def test_manual_subscription_results_normalize(service, result, classification):
    record = {"mode": "manual", "service": service, "operator_result": result}
    summary = normalize_health_response(_subscription_config(service), record)
    assert summary["provider"] == "interactive_subscription"
    assert summary["mode"] == "manual_operator"
    assert summary["classification"] == classification
    assert summary["manual_result"] == result
    assert summary["service"] == service
    assert "status" not in summary
    assert "model_count" not in summary


def test_manual_note_is_accepted_but_never_echoed():
    summary = normalize_health_response(_subscription_config(), MANUAL_OK)
    rendered = to_json(summary)
    assert MANUAL_OK["note"] not in rendered


def test_subscription_rejects_an_automated_http_response():
    for automated in (
        {"status": 200, "body": OPENAI_BODY},
        {"mode": "manual", "service": "chatgpt", "operator_result": "ok", "status": 200},
        {"mode": "manual", "service": "chatgpt", "operator_result": "ok", "body": {}},
        {"mode": "manual", "service": "chatgpt", "operator_result": "ok", "headers": {}},
    ):
        with pytest.raises(AutomatedSubscriptionResponseError):
            normalize_health_response(_subscription_config(), automated)


def test_manual_record_service_must_match_the_configuration():
    record = {"mode": "manual", "service": "chatgpt", "operator_result": "ok"}
    with pytest.raises(ResponseValidationError):
        normalize_health_response(_subscription_config("claude"), record)


@pytest.mark.parametrize(
    "record",
    [
        {"mode": "manual", "service": "chatgpt", "operator_result": "maybe"},
        {"mode": "automated", "service": "chatgpt", "operator_result": "ok"},
        {"mode": "manual", "service": "gemini", "operator_result": "ok"},
        {"mode": "manual", "service": "chatgpt"},
        {},
        None,
        "ok",
    ],
)
def test_malformed_manual_records_are_rejected(record):
    with pytest.raises(ProviderConfigError):
        normalize_health_response(_subscription_config(), record)


def test_api_configs_reject_manual_records():
    with pytest.raises(ResponseValidationError):
        normalize_health_response(OPENAI_CONFIG, dict(MANUAL_OK))


# --- redaction: the raw response never escapes --------------------------------


def test_summary_never_contains_the_raw_response():
    response = {"status": 200, "body": OLLAMA_BODY}
    summary = normalize_health_response({"provider": "ollama_local"}, response)
    rendered = to_json(summary)
    assert "llama3.2:latest" not in rendered  # no model ids
    assert "digest" not in rendered
    assert "body" not in summary
    assert "response" not in summary


def test_error_messages_from_the_provider_are_not_echoed():
    body = {
        "error": {
            "message": "key was rejected by the upstream account gateway",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }
    summary = normalize_health_response(OPENAI_CONFIG, {"status": 401, "body": body})
    assert "gateway" not in to_json(summary)


def test_response_hash_is_canonical_sha256_of_the_body():
    response = {"status": 200, "body": OPENAI_BODY}
    summary = normalize_health_response(OPENAI_CONFIG, response)
    expected = hashlib.sha256(to_json(OPENAI_BODY).encode("utf-8")).hexdigest()
    assert summary["response_sha256"] == expected


# --- secret screening ----------------------------------------------------------


def _assert_absent_from_exception_chain(exc, needle):
    """Walk the full exception surface — args, repr, attributes, and the
    __cause__/__context__ chain — asserting the needle never appears."""
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        assert needle not in repr(current)
        assert needle not in repr(getattr(current, "args", ()))
        for value in vars(current).values():
            if not isinstance(value, BaseException):
                assert needle not in repr(value)
        stack.append(current.__cause__)
        stack.append(current.__context__)


def test_secret_echoing_error_bodies_are_rejected_and_never_leaked():
    secret = "sk-evil-material-that-must-never-appear"
    body = {
        "error": {
            "message": f"Incorrect API key provided: {secret}",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }
    with pytest.raises(ProviderConfigError) as excinfo:
        normalize_health_response(OPENAI_CONFIG, {"status": 401, "body": body})
    _assert_absent_from_exception_chain(excinfo.value, secret)


def test_secret_shaped_model_ids_are_rejected():
    body = {"object": "list", "data": [{"id": "sk-not-a-model", "object": "model"}]}
    with pytest.raises(ProviderConfigError):
        normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": body})


def test_secret_shaped_manual_notes_are_rejected():
    record = {
        "mode": "manual",
        "service": "chatgpt",
        "operator_result": "ok",
        "note": "session token was eyJhbGciOiJIUzI1NiJ9",
    }
    with pytest.raises(ProviderConfigError):
        normalize_health_response(_subscription_config(), record)


# --- malformed and tampered bodies (fail closed) --------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"object": "list"},
        {"object": "page", "data": []},
        {"object": "list", "data": "gpt-4o"},
        {"object": "list", "data": [{"object": "model"}]},  # entry missing id
        {"object": "list", "data": [{"id": 42, "object": "model"}]},
        {"object": "list", "data": [{"id": "gpt-4o", "surprise": True}]},
        {"object": "list", "data": [], "extra": 1},
        {"object": "list", "data": ["gpt-4o"]},
        ANTHROPIC_BODY,  # wrong provider's shape
    ],
)
def test_malformed_openai_bodies_are_rejected(body):
    with pytest.raises(ResponseValidationError):
        normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": body})


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": [{"type": "completion", "id": "x"}], "has_more": False},
        {"data": [{"type": "model"}], "has_more": False},
        {"data": [], "has_more": "no"},
        {"data": [], "unexpected": True},
        OPENAI_BODY,  # wrong provider's shape
    ],
)
def test_malformed_anthropic_bodies_are_rejected(body):
    with pytest.raises(ResponseValidationError):
        normalize_health_response(
            {"provider": "anthropic_api"}, {"status": 200, "body": body}
        )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"models": "llama"},
        {"models": [{"size": 3}]},  # entry names no model
        {"models": [{"name": "llama3.2", "surprise": True}]},
        {"models": [], "extra": 1},
        OPENAI_BODY,  # wrong provider's shape
    ],
)
def test_malformed_ollama_bodies_are_rejected(body):
    with pytest.raises(ResponseValidationError):
        normalize_health_response(
            {"provider": "ollama_local"}, {"status": 200, "body": body}
        )


@pytest.mark.parametrize(
    "response",
    [
        None,
        "response",
        42,
        {},
        {"status": 200},
        {"body": OPENAI_BODY},
        {"status": "200", "body": OPENAI_BODY},
        {"status": 200, "body": "not-a-dict"},
        {"status": 200, "body": OPENAI_BODY, "headers": {}},
    ],
)
def test_malformed_response_envelopes_are_rejected(response):
    with pytest.raises(ResponseValidationError):
        normalize_health_response(OPENAI_CONFIG, response)


def test_oversized_model_lists_are_rejected_as_out_of_bounds():
    body = {
        "object": "list",
        "data": [{"id": f"model-{i}", "object": "model"} for i in range(513)],
    }
    with pytest.raises(ResponseValidationError):
        normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": body})


def test_oversized_strings_are_rejected_as_out_of_bounds():
    body = {"object": "list", "data": [{"id": "m" * 300, "object": "model"}]}
    with pytest.raises(ResponseValidationError):
        normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": body})


def test_invalid_configs_are_rejected_before_the_response_is_touched():
    for config in (None, {}, {"provider": "unknown"}, {"provider": "openai_api", "live_enabled": True}):
        with pytest.raises(ProviderConfigError):
            normalize_health_response(config, {"status": 200, "body": OPENAI_BODY})


# --- determinism, purity, structure ---------------------------------------------


def test_normalization_is_byte_deterministic():
    a = normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": OPENAI_BODY})
    b = normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": OPENAI_BODY})
    assert a == b
    assert to_json(a) == to_json(b)
    parsed = json.loads(to_json(a))
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_normalization_never_reads_environment(monkeypatch):
    sentinel = "sk-proof-value-must-never-appear"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    with_env = normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": OPENAI_BODY})
    monkeypatch.delenv("OPENAI_API_KEY")
    without_env = normalize_health_response(OPENAI_CONFIG, {"status": 200, "body": OPENAI_BODY})
    assert with_env == without_env
    assert sentinel not in to_json(with_env)


def test_normalization_mutates_neither_config_nor_response():
    config = copy.deepcopy(OPENAI_CONFIG)
    response = {"status": 200, "body": copy.deepcopy(OPENAI_BODY)}
    snapshot = copy.deepcopy(response)
    normalize_health_response(config, response)
    assert config == OPENAI_CONFIG
    assert response == snapshot


def test_all_new_errors_are_provider_config_errors():
    assert issubclass(ResponseValidationError, ProviderConfigError)
    assert issubclass(AutomatedSubscriptionResponseError, ResponseValidationError)


def test_module_source_imports_no_io_machinery():
    source = RESPONSE_SOURCE.read_text(encoding="utf-8")
    for forbidden in (
        "import socket",
        "import urllib",
        "from urllib",
        "import http",
        "from http",
        "import requests",
        "import subprocess",
        "from subprocess",
        "os.environ",
        "getenv",
        "import time",
        "import random",
        "datetime",
        "open(",
        "import os",
        "import pathlib",
    ):
        assert forbidden not in source, forbidden
