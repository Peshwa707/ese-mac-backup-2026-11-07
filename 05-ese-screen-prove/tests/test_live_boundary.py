import os

import pytest

from screen_prove.live_boundary import (
    LIVE_SMOKE_OPT_IN_ENV_VAR,
    ConnectorBoundary,
    LiveBoundaryAdapter,
    LiveExecutionNotAuthorized,
    LiveRequestDescription,
    ProviderBoundary,
    live_smoke_authorized,
    require_live_smoke_authorization,
)


def test_live_smoke_not_authorized_by_default_when_env_var_absent():
    assert live_smoke_authorized({}) is False


def test_live_smoke_not_authorized_for_falsy_or_near_miss_values():
    for value in ("0", "true", "TRUE", "yes", "on", " 1", "1 ", ""):
        assert live_smoke_authorized({LIVE_SMOKE_OPT_IN_ENV_VAR: value}) is False


def test_live_smoke_authorized_only_for_exact_opt_in_value():
    assert live_smoke_authorized({LIVE_SMOKE_OPT_IN_ENV_VAR: "1"}) is True


def test_live_smoke_authorized_reads_process_environment_by_default(monkeypatch):
    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)
    assert live_smoke_authorized() is False

    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    assert live_smoke_authorized() is True


def test_require_live_smoke_authorization_raises_without_opt_in():
    with pytest.raises(LiveExecutionNotAuthorized, match=LIVE_SMOKE_OPT_IN_ENV_VAR):
        require_live_smoke_authorization({})


def test_require_live_smoke_authorization_passes_with_opt_in():
    require_live_smoke_authorization({LIVE_SMOKE_OPT_IN_ENV_VAR: "1"})


def test_live_boundary_adapter_blocks_execution_without_opt_in(monkeypatch):
    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)

    class TrackingAdapter(LiveBoundaryAdapter):
        def _execute_request_live(self, request):
            raise AssertionError("network-capable code must not run without opt-in")

    adapter = TrackingAdapter()
    request = LiveRequestDescription(
        boundary="provider", name="fixture", version="1", operation="screen", parameters={}
    )

    with pytest.raises(LiveExecutionNotAuthorized):
        adapter.execute_live(request)


def test_live_boundary_adapter_default_implementation_raises_not_implemented(monkeypatch):
    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    adapter = LiveBoundaryAdapter()
    request = LiveRequestDescription(
        boundary="connector", name="fixture", version="1", operation="fetch", parameters={}
    )

    with pytest.raises(NotImplementedError):
        adapter.execute_live(request)


def test_live_boundary_adapter_invokes_subclass_only_after_opt_in(monkeypatch):
    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")

    calls = []

    class RecordingAdapter(LiveBoundaryAdapter):
        def _execute_request_live(self, request):
            calls.append(request)
            return {"ok": True}

    adapter = RecordingAdapter()
    request = LiveRequestDescription(
        boundary="provider", name="fixture", version="1", operation="screen", parameters={"record_id": "r1"}
    )

    result = adapter.execute_live(request)

    assert result == {"ok": True}
    assert calls == [request]


def test_provider_boundary_subclass_gates_live_execution(monkeypatch):
    class FixtureProvider(ProviderBoundary):
        name = "fixture-provider"
        version = "1"
        provider = "offline"
        model = "fixture"

        def describe_request(self, record):
            return LiveRequestDescription(
                boundary="provider", name=self.name, version=self.version,
                operation="screen", parameters={"record_id": record["record_id"]},
            )

        def _execute_request_live(self, request):
            return {"decision": "abstain", "reason": "not executed offline"}

    provider = FixtureProvider()
    description = provider.describe_request({"record_id": "r1"})
    assert description.parameters == {"record_id": "r1"}

    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)
    with pytest.raises(LiveExecutionNotAuthorized):
        provider.execute_live(description)

    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    assert provider.execute_live(description) == {
        "decision": "abstain",
        "reason": "not executed offline",
    }


def test_connector_boundary_subclass_gates_live_execution(monkeypatch):
    class FixtureConnector(ConnectorBoundary):
        name = "fixture-connector"
        source = "offline"

        def describe_query(self, params):
            return LiveRequestDescription(
                boundary="connector", name=self.name, version="1",
                operation="fetch", parameters=dict(params),
            )

        def _execute_request_live(self, request):
            return {"records": []}

    connector = FixtureConnector()
    description = connector.describe_query({"query": "term"})
    assert description.operation == "fetch"

    monkeypatch.delenv(LIVE_SMOKE_OPT_IN_ENV_VAR, raising=False)
    with pytest.raises(LiveExecutionNotAuthorized):
        connector.fetch_live(description)

    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")
    assert connector.fetch_live(description) == {"records": []}


def test_private_hook_is_an_implementation_convention_not_a_second_gate(monkeypatch):
    # The documented guarantee is on the public adapter entry point. The
    # private hook remains ordinary in-process Python and is intentionally not
    # presented as a security boundary.
    monkeypatch.setenv(LIVE_SMOKE_OPT_IN_ENV_VAR, "1")

    calls = []

    class RecordingAdapter(LiveBoundaryAdapter):
        def _execute_request_live(self, request):
            calls.append(request)
            return {"ok": True}

    adapter = RecordingAdapter()
    request = LiveRequestDescription(
        boundary="provider", name="fixture", version="1", operation="screen", parameters={}
    )

    assert adapter._execute_request_live(request) == {"ok": True}
    assert calls == [request]


def test_provider_boundary_rejects_a_subclass_that_overrides_execute_live():
    with pytest.raises(TypeError, match="execute_live"):

        class BypassProvider(ProviderBoundary):
            name = "bypass-provider"
            version = "1"
            provider = "offline"
            model = "fixture"

            def describe_request(self, record):
                return LiveRequestDescription(
                    boundary="provider", name=self.name, version=self.version,
                    operation="screen", parameters={},
                )

            def execute_live(self, request):
                # Attempts to bypass require_live_smoke_authorization by
                # overriding the gate-enforcing entry point directly.
                return {"decision": "include", "reason": "gate bypassed"}


def test_connector_boundary_rejects_a_subclass_that_overrides_fetch_live():
    with pytest.raises(TypeError, match="fetch_live"):

        class BypassConnector(ConnectorBoundary):
            name = "bypass-connector"
            source = "offline"

            def describe_query(self, params):
                return LiveRequestDescription(
                    boundary="connector", name=self.name, version="1",
                    operation="fetch", parameters=dict(params),
                )

            def fetch_live(self, request):
                # Attempts to bypass require_live_smoke_authorization by
                # overriding the gate-enforcing entry point directly.
                return {"records": ["gate bypassed"]}


def test_structural_lookalike_is_not_a_provider_boundary():
    # A class that merely defines matching attribute/method names (including
    # an execute_live that never calls the gate) must not be usable in place
    # of a real ProviderBoundary: the contract is nominal (inheritance-based)
    # rather than structural, so isinstance correctly rejects it.
    class LookalikeProvider:
        name = "lookalike-provider"
        version = "1"
        provider = "offline"
        model = "fixture"

        def describe_request(self, record):
            return LiveRequestDescription(
                boundary="provider", name=self.name, version=self.version,
                operation="screen", parameters={},
            )

        def execute_live(self, request):
            return {"decision": "include", "reason": "no gate called"}

    assert not isinstance(LookalikeProvider(), ProviderBoundary)


def test_structural_lookalike_is_not_a_connector_boundary():
    class LookalikeConnector:
        name = "lookalike-connector"
        source = "offline"

        def describe_query(self, params):
            return LiveRequestDescription(
                boundary="connector", name=self.name, version="1",
                operation="fetch", parameters=dict(params),
            )

        def fetch_live(self, request):
            return {"records": ["no gate called"]}

    assert not isinstance(LookalikeConnector(), ConnectorBoundary)


def test_opt_in_env_var_is_not_set_in_the_default_test_process():
    # Guards the "default execution stays network-free and credential-free"
    # requirement: nothing in this repository's default test run may export
    # the live-smoke opt-in flag.
    assert os.environ.get(LIVE_SMOKE_OPT_IN_ENV_VAR) != "1"
