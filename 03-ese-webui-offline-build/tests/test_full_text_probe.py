import inspect
import json
import copy

import pytest

import full_text_probe
from full_text_access import ConnectorInputError
from full_text_probe import (
    FULL_TEXT_PROBE_FIXTURE_ID,
    AccessProbeAuthorizationError,
    AccessProbeExecutionError,
    ManualAccessRequiredError,
    build_probe_plan,
    execute_authorized_probe,
)
from full_text_config import validate_access_config


def citation():
    return {
        "doi": "10.1000/ABC.1",
        "pmid": "39551604",
        "title": "A bounded fixture article",
    }


def profile(connector_id):
    return validate_access_config(
        {
            "connectors": [
                {
                    "connector_id": connector_id,
                    "enabled": True,
                    "mode": "api_key_reference",
                    "credential_ref": {
                        "kind": "env",
                        "name": f"{connector_id.upper()}_API_KEY",
                    },
                }
            ]
        }
    )


def authorization(connector_id):
    return {
        "confirmed": True,
        "fixture_id": FULL_TEXT_PROBE_FIXTURE_ID,
        "statement": (
            "I authorize metadata_or_entitlement_probe for provider "
            f"{connector_id} using fixture {FULL_TEXT_PROBE_FIXTURE_ID}."
        ),
    }


class Resolver:
    def __init__(self, value="fixture-api-key"):
        self.value = value
        self.refs = []

    def resolve(self, ref):
        self.refs.append(ref)
        return self.value


class Transport:
    def __init__(self, response=None):
        self.response = response or {"status": 200, "body": {"fixture": "ok"}}
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.response


@pytest.mark.parametrize("connector_id", ["scopus", "embase"])
def test_probe_plan_is_deterministic_and_metadata_only(connector_id):
    first = build_probe_plan(citation(), profile(connector_id))
    second = build_probe_plan(citation(), profile(connector_id))
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert first["connector_id"] == connector_id
    assert first["operation"] == "metadata_or_entitlement_probe"
    assert first["full_text_verified"] is False
    assert first["request"]["host"] == "api.elsevier.com"
    assert "fixture-api-key" not in json.dumps(first)
    if connector_id == "scopus":
        assert first["request"]["route"] == "/content/search/scopus"
        assert first["request"]["params"]["query"] == "DOI(10.1000/abc.1)"
    else:
        assert first["request"]["route"] == "/content/embase/article/doi/10.1000%2Fabc.1"


def test_probe_rejects_manual_or_unconfigured_routes():
    manual = validate_access_config(
        {"connectors": [{"connector_id": "ezproxy", "enabled": True, "mode": "manual_browser"}]}
    )
    with pytest.raises(ManualAccessRequiredError):
        build_probe_plan(citation(), manual)
    with pytest.raises(ConnectorInputError):
        build_probe_plan(citation(), {"connectors": []})


def test_probe_requires_stable_identity():
    with pytest.raises(ConnectorInputError):
        build_probe_plan({"title": "No persistent id"}, profile("scopus"))


def test_authorized_probe_burns_attempt_before_resolution_and_transport():
    plan = build_probe_plan(citation(), profile("scopus"))
    resolver = Resolver()
    transport = Transport()
    used = set()
    result = execute_authorized_probe(
        plan,
        authorization=authorization("scopus"),
        used_attempts=used,
        resolver=resolver,
        transport=transport,
    )
    assert result["full_text_verified"] is False
    assert used == {plan["probe_id"]}
    assert resolver.refs == [{"kind": "env", "name": "SCOPUS_API_KEY"}]
    assert transport.requests[0]["headers"] == {"X-ELS-APIKey": "fixture-api-key"}
    assert "headers" not in result


def test_probe_requires_exact_authorization_and_never_burns_on_auth_failure():
    plan = build_probe_plan(citation(), profile("embase"))
    with pytest.raises(AccessProbeAuthorizationError):
        execute_authorized_probe(
            plan,
            authorization={"confirmed": True},
            used_attempts=set(),
            resolver=Resolver(),
            transport=Transport(),
        )


def test_probe_requires_explicit_runtime_and_burns_before_runtime_failure():
    plan = build_probe_plan(citation(), profile("scopus"))
    used = set()
    with pytest.raises(AccessProbeExecutionError):
        execute_authorized_probe(
            plan,
            authorization=authorization("scopus"),
            used_attempts=used,
            resolver=None,
            transport=None,
        )
    assert used == set()


def test_probe_redacts_resolver_transport_failures_and_secret_like_response():
    plan = build_probe_plan(citation(), profile("embase"))
    used = set()

    class FailingResolver:
        def resolve(self, ref):
            raise RuntimeError("fixture-api-key should not escape")

    with pytest.raises(AccessProbeExecutionError) as exc:
        execute_authorized_probe(
            plan,
            authorization=authorization("embase"),
            used_attempts=used,
            resolver=FailingResolver(),
            transport=Transport(),
        )
    assert "fixture-api-key" not in str(exc.value)
    assert used == {plan["probe_id"]}
    used.clear()
    with pytest.raises(AccessProbeExecutionError):
        execute_authorized_probe(
            plan,
            authorization=authorization("embase"),
            used_attempts=used,
            resolver=Resolver(),
            transport=Transport(response={"status": 200, "body": {"token": "sk-live-secret"}}),
        )
    assert used == {plan["probe_id"]}


def test_probe_rejects_self_consistent_forged_route():
    plan = build_probe_plan(citation(), profile("scopus"))
    forged = copy.deepcopy(plan)
    forged["request"]["route"] = "/content/search/forged"
    forged["probe_id"] = full_text_probe._hash_body(full_text_probe._probe_body(forged))
    with pytest.raises(AccessProbeExecutionError):
        execute_authorized_probe(
            forged,
            authorization=authorization("scopus"),
            used_attempts=set(),
            resolver=Resolver(),
            transport=Transport(),
        )


def test_probe_rejects_header_injection_from_resolver():
    plan = build_probe_plan(citation(), profile("scopus"))

    class HeaderInjectionResolver:
        def resolve(self, ref):
            return "fixture\r\nX-Leak: 1"

    with pytest.raises(AccessProbeExecutionError):
        execute_authorized_probe(
            plan,
            authorization=authorization("scopus"),
            used_attempts=set(),
            resolver=HeaderInjectionResolver(),
            transport=Transport(),
        )

def test_probe_module_has_no_default_network_or_credential_read_path():
    source = inspect.getsource(full_text_probe).lower()
    assert "os.environ" not in source
    assert "urllib.request" not in source
    assert "requests." not in source
    assert "subprocess" not in source
