"""Deterministic tests for the opt-in execution edge.

Every resolver and transport here is an in-memory fixture. No environment
value, keychain item, provider endpoint, or subscription session is touched.
"""

from __future__ import annotations

import copy
import inspect
from urllib.error import HTTPError

import pytest

from provider_call_plan import build_call_plan
from provider_executor import (
    EXECUTION_FIXTURE_ID,
    AuthorizationRequiredError,
    CredentialResolutionError,
    EnvironmentCredentialResolver,
    LiveExecutionError,
    ManualSubscriptionRequiredError,
    TransportRequiredError,
    UrllibTransport,
    execute_authorized_health_check,
)
from provider_gate import GateReusedError, issue_gate
from provider_manifest import build_health_request
from provider_health import to_json
import provider_executor


RUN_ID = "abc123def456"
SECRET = "fixture-secret-never-returned"


def _config(provider: str):
    config = {"provider": provider}
    if provider in {
        "openai_api",
        "anthropic_api",
        "ollama_cloud",
        "moonshot_kimi_api",
        "deepseek_api",
    }:
        config["credential_ref"] = {"kind": "env", "name": "FIXTURE_API_KEY"}
    return config


def _gate_and_plan(config):
    gate = issue_gate(
        config,
        run_id=RUN_ID,
        action="health_check",
        operator_ack={
            "confirmed": True,
            "statement": f"I authorize health_check for provider {config['provider']}",
        },
    )
    plan = build_call_plan(
        config,
        build_health_request(config),
        gate,
        run_id=RUN_ID,
        action="health_check",
    )
    return gate, plan


def _authorization(provider: str, *, fixture_id=EXECUTION_FIXTURE_ID, confirmed=True):
    return {
        "confirmed": confirmed,
        "fixture_id": fixture_id,
        "statement": (
            f"I authorize health_check for provider {provider} using fixture "
            f"{fixture_id}."
        ),
    }


def _response(provider: str):
    if provider == "anthropic_api":
        return {"status": 200, "body": {"data": [{"type": "model", "id": "fixture-model"}]}}
    if provider in {"ollama_local", "ollama_cloud"}:
        return {"status": 200, "body": {"models": [{"name": "fixture-model"}]}}
    return {"status": 200, "body": {"object": "list", "data": [{"id": "fixture-model"}]}}


class FixtureResolver:
    def __init__(self, value=SECRET):
        self.value = value
        self.references = []

    def resolve(self, reference):
        self.references.append(copy.deepcopy(reference))
        return self.value


class FixtureTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def send(self, request):
        self.requests.append(copy.deepcopy(request))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    "provider",
    [
        "openai_api",
        "anthropic_api",
        "ollama_local",
        "ollama_cloud",
        "moonshot_kimi_api",
        "deepseek_api",
    ],
)
def test_authorized_health_check_supports_each_api_mode_with_fixture_runtime(provider):
    config = _config(provider)
    gate, plan = _gate_and_plan(config)
    resolver = FixtureResolver()
    transport = FixtureTransport(response=_response(provider))
    used_gates = set()

    result = execute_authorized_health_check(
        plan,
        config,
        gate,
        authorization=_authorization(provider),
        used_gates=used_gates,
        resolver=resolver,
        transport=transport,
    )

    assert result["fixture_id"] == EXECUTION_FIXTURE_ID
    assert result["result"]["classification"] == "ok"
    assert len(transport.requests) == 1
    assert gate["gate_id"] in used_gates
    if provider == "ollama_local":
        assert resolver.references == []
        assert transport.requests[0]["headers"] == {}
    else:
        assert resolver.references == [config["credential_ref"]]
        assert transport.requests[0]["headers"]
        assert SECRET in next(iter(transport.requests[0]["headers"].values()))
    assert SECRET not in to_json(result)
    assert "request" not in result


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        {},
        {"confirmed": False, "fixture_id": EXECUTION_FIXTURE_ID, "statement": ""},
        {"confirmed": True, "fixture_id": "wrong-fixture", "statement": "health_check"},
    ],
)
def test_authorization_is_revalidated_inside_execution_hook(authorization):
    config = _config("openai_api")
    gate, plan = _gate_and_plan(config)
    transport = FixtureTransport(response=_response("openai_api"))
    with pytest.raises(AuthorizationRequiredError):
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=authorization,
            used_gates=set(),
            resolver=FixtureResolver(),
            transport=transport,
        )
    assert transport.requests == []


def test_authorization_statement_must_name_provider_action_and_fixture():
    config = _config("openai_api")
    gate, plan = _gate_and_plan(config)
    authorization = _authorization("deepseek_api")
    with pytest.raises(AuthorizationRequiredError):
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=authorization,
            used_gates=set(),
            resolver=FixtureResolver(),
            transport=FixtureTransport(response=_response("openai_api")),
        )


def test_missing_runtime_dependencies_fail_before_gate_burn():
    config = _config("openai_api")
    gate, plan = _gate_and_plan(config)
    used_gates = set()
    with pytest.raises(CredentialResolutionError):
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=_authorization("openai_api"),
            used_gates=used_gates,
            resolver=None,
            transport=FixtureTransport(response=_response("openai_api")),
        )
    assert used_gates == set()
    with pytest.raises(TransportRequiredError):
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=_authorization("openai_api"),
            used_gates=used_gates,
            resolver=FixtureResolver(),
            transport=None,
        )
    assert used_gates == set()


def test_gate_burns_before_credential_resolution_and_transport_attempt():
    config = _config("openai_api")
    gate, plan = _gate_and_plan(config)
    used_gates = set()
    resolver = FixtureResolver()
    transport = FixtureTransport(response=_response("openai_api"))
    execute_authorized_health_check(
        plan,
        config,
        gate,
        authorization=_authorization("openai_api"),
        used_gates=used_gates,
        resolver=resolver,
        transport=transport,
    )
    with pytest.raises(GateReusedError):
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=_authorization("openai_api"),
            used_gates=used_gates,
            resolver=resolver,
            transport=transport,
        )
    assert len(transport.requests) == 1


def test_resolver_failure_burns_gate_without_echoing_secret():
    config = _config("openai_api")
    gate, plan = _gate_and_plan(config)
    used_gates = set()

    class FailingResolver(FixtureResolver):
        def resolve(self, reference):
            self.references.append(copy.deepcopy(reference))
            raise RuntimeError(SECRET)

    with pytest.raises(LiveExecutionError) as exc:
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=_authorization("openai_api"),
            used_gates=used_gates,
            resolver=FailingResolver(),
            transport=FixtureTransport(response=_response("openai_api")),
        )
    assert SECRET not in str(exc.value)
    assert gate["gate_id"] in used_gates


def test_transport_failure_is_redacted_and_gate_is_burned():
    config = _config("openai_api")
    gate, plan = _gate_and_plan(config)
    used_gates = set()
    with pytest.raises(LiveExecutionError) as exc:
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=_authorization("openai_api"),
            used_gates=used_gates,
            resolver=FixtureResolver(),
            transport=FixtureTransport(error=RuntimeError(SECRET)),
        )
    assert SECRET not in str(exc.value)
    assert gate["gate_id"] in used_gates


def test_subscription_mode_stays_manual_and_does_not_burn_gate():
    config = {"provider": "interactive_subscription", "service": "claude"}
    gate, plan = _gate_and_plan(config)
    used_gates = set()
    with pytest.raises(ManualSubscriptionRequiredError):
        execute_authorized_health_check(
            plan,
            config,
            gate,
            authorization=_authorization("interactive_subscription"),
            used_gates=used_gates,
            resolver=FixtureResolver(),
            transport=FixtureTransport(response={}),
        )
    assert used_gates == set()


def test_executor_has_no_default_io_or_credential_loading_path():
    source = inspect.getsource(provider_executor).lower()
    assert "class environmentcredentialresolver" in source
    assert "class urllibtransport" in source
    assert "subprocess" not in source


def test_environment_resolver_reads_only_the_named_reference(monkeypatch):
    monkeypatch.setenv("FIXTURE_API_KEY", SECRET)
    resolver = EnvironmentCredentialResolver()
    assert resolver.resolve({"kind": "env", "name": "FIXTURE_API_KEY"}) == SECRET
    with pytest.raises(CredentialResolutionError):
        resolver.resolve({"kind": "env", "name": "MISSING_FIXTURE_KEY"})


def test_environment_resolver_rejects_keychain_without_accessing_it():
    with pytest.raises(CredentialResolutionError):
        EnvironmentCredentialResolver().resolve(
            {"kind": "keychain", "service": "fixture", "account": "fixture"}
        )


def test_urllib_transport_uses_bounded_fake_response(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            assert size > 0
            return b'{"object":"list","data":[{"id":"fixture-model"}]}'

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse()

    class FakeOpener:
        def open(self, request, timeout):
            return fake_urlopen(request, timeout)

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: FakeOpener())
    response = UrllibTransport(timeout_seconds=3).send(
        {
            "execution_version": 1,
            "fixture_id": EXECUTION_FIXTURE_ID,
            "provider": "openai_api",
            "operation": "health_check",
            "method": "GET",
            "host": "api.openai.com",
            "route": "/v1/models",
            "headers": {"Authorization": "Bearer fixture"},
        }
    )
    assert response == {
        "status": 200,
        "body": {"object": "list", "data": [{"id": "fixture-model"}]},
    }
    assert captured == {
        "url": "https://api.openai.com/v1/models",
        "method": "GET",
        "headers": {"Authorization": "Bearer fixture"},
        "timeout": 3,
    }


def test_urllib_transport_rejects_cookie_or_noncanonical_headers():
    with pytest.raises(TransportRequiredError):
        UrllibTransport().send(
            {
                "execution_version": 1,
                "fixture_id": EXECUTION_FIXTURE_ID,
                "provider": "openai_api",
                "operation": "health_check",
                "method": "GET",
                "host": "api.openai.com",
                "route": "/v1/models",
                "headers": {"Cookie": "opaque-session"},
            }
        )


def test_urllib_transport_refuses_redirects_before_reissuing_headers():
    from provider_executor import _NoRedirectHandler

    handler = _NoRedirectHandler()
    request = provider_executor.urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": "Bearer fixture"},
        method="GET",
    )
    with pytest.raises(HTTPError):
        handler.redirect_request(
            request,
            None,
            302,
            "redirect",
            {"Location": "https://evil.example/steal"},
            "https://evil.example/steal",
        )


def test_urllib_transport_rejects_control_characters_in_route():
    with pytest.raises(TransportRequiredError):
        UrllibTransport().send(
            {
                "execution_version": 1,
                "fixture_id": EXECUTION_FIXTURE_ID,
                "provider": "openai_api",
                "operation": "health_check",
                "method": "GET",
                "host": "api.openai.com",
                "route": "/v1/models\nX-Injected: yes",
                "headers": {},
            }
        )


def test_urllib_transport_binds_provider_to_exact_host_and_route():
    with pytest.raises(TransportRequiredError):
        UrllibTransport().send(
            {
                "execution_version": 1,
                "fixture_id": EXECUTION_FIXTURE_ID,
                "provider": "openai_api",
                "operation": "health_check",
                "method": "GET",
                "host": "api.anthropic.com",
                "route": "/v1/models",
                "headers": {"Authorization": "Bearer fixture"},
            }
        )


def test_urllib_transport_rejects_header_injection_and_localhost_auth():
    with pytest.raises(TransportRequiredError):
        UrllibTransport().send(
            {
                "execution_version": 1,
                "fixture_id": EXECUTION_FIXTURE_ID,
                "provider": "openai_api",
                "operation": "health_check",
                "method": "GET",
                "host": "api.openai.com",
                "route": "/v1/models",
                "headers": {"Authorization": "Bearer fixture\r\nX-Leak: 1"},
            }
        )
    with pytest.raises(TransportRequiredError):
        UrllibTransport().send(
            {
                "execution_version": 1,
                "fixture_id": EXECUTION_FIXTURE_ID,
                "provider": "ollama_local",
                "operation": "health_check",
                "method": "GET",
                "host": "127.0.0.1:11434",
                "route": "/api/tags",
                "headers": {"Authorization": "Bearer fixture"},
            }
        )
