"""Boundary contracts for opt-in, offline-by-default live smoke tests.

Nothing in this module performs network I/O, reads a live credential, or
executes a live provider/connector call by itself. It defines two things:

1. The shape a live LLM screening *provider* adapter and a live literature or
   registry *connector* must implement to be plugged into a future opt-in
   smoke test (`ProviderBoundary`, `ConnectorBoundary`).
2. The single opt-in gate every live call must pass through
   (`require_live_smoke_authorization`) before any network-capable code may
   run.

`ProviderBoundary` and `ConnectorBoundary` are deliberately *not* structural
(`typing.Protocol`) contracts. A structural contract can be satisfied by any
class that merely happens to define matching method names, including one
that never calls `require_live_smoke_authorization`, so it cannot document
the normal gated path clearly. Instead both are concrete classes that inherit
the gate-enforcing `LiveBoundaryAdapter`: a subclass gets the public
`execute_live`/`fetch_live` entry points from the adapter and implements the
private `_execute_request_live` hook. `LiveBoundaryAdapter.__init_subclass__`
rejects, at class-definition time, subclasses that redefine those public
entry points. The gate applies to the public entry points; the private hook
is an in-process implementation convention, not a security boundary against
hostile Python code that directly calls or reassigns methods.

Default CLI and test execution in this repository never sets
`SCREEN_PROVE_ALLOW_LIVE_SMOKE`, so the gate always raises and no live
boundary implementation is ever invoked. This module ships no concrete
provider or connector implementation; see the work-package queue in
`docs/AUTONOMOUS_BUILD_LOOP.md` for the follow-up that adds bounded canary
specifications without executing them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

LIVE_SMOKE_OPT_IN_ENV_VAR = "SCREEN_PROVE_ALLOW_LIVE_SMOKE"
_AUTHORIZED_VALUE = "1"


class LiveExecutionNotAuthorized(RuntimeError):
    """Raised when live boundary execution is attempted without explicit opt-in."""


def live_smoke_authorized(env: Mapping[str, str] | None = None) -> bool:
    """Return True only if the exact opt-in value is present.

    `env` defaults to the real process environment. Any value other than the
    exact string `"1"` (including empty, whitespace-padded, or other common
    truthy spellings) is treated as not authorized, so a typo or a loosely
    truthy environment variable can never accidentally enable live execution.
    """
    source = os.environ if env is None else env
    return source.get(LIVE_SMOKE_OPT_IN_ENV_VAR) == _AUTHORIZED_VALUE


def require_live_smoke_authorization(env: Mapping[str, str] | None = None) -> None:
    """Raise `LiveExecutionNotAuthorized` unless the opt-in flag is exactly set.

    This is the single gate every live boundary implementation must call
    before performing any network-capable work. It checks one explicit,
    caller-controlled flag; it never reads or inspects any credential.
    """
    if not live_smoke_authorized(env):
        raise LiveExecutionNotAuthorized(
            f"live smoke execution requires {LIVE_SMOKE_OPT_IN_ENV_VAR}=1; "
            "default (offline) execution does not set it"
        )


@dataclass(frozen=True)
class LiveRequestDescription:
    """A deterministic, offline description of what a live call would send.

    Building one never performs network I/O, so it is safe to construct and
    inspect during default (non-opt-in) test runs and code review.
    """

    boundary: str  # "provider" or "connector"
    name: str
    version: str
    operation: str
    parameters: dict[str, Any]


class LiveBoundaryAdapter:
    """Base class that enforces the opt-in gate ahead of any live call.

    This is the sole sanctioned entry point for live execution in this
    module. Subclasses implement their network-capable logic in the private
    `_execute_request_live` hook; the public `execute_live` and `fetch_live`
    methods always check authorization first and then delegate to that hook.

    `__init_subclass__` rejects a subclass that defines `execute_live` or
    `fetch_live` in its own class body. This protects the documented public
    adapter path from accidental overrides. It is not intended to defend
    against hostile in-process Python code; callers must use the public
    methods for the opt-in guarantee.
    """

    _GATED_METHOD_NAMES = ("execute_live", "fetch_live")
    _LIVE_HOOK_NAME = "_execute_request_live"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for gated_method_name in cls._GATED_METHOD_NAMES:
            if gated_method_name in cls.__dict__:
                raise TypeError(
                    f"{cls.__name__} must not define {gated_method_name!r}; "
                    "it is a gate-enforcing method inherited from "
                    "LiveBoundaryAdapter and overriding it would let a "
                    "subclass bypass require_live_smoke_authorization(). "
                    "Implement _execute_request_live instead."
                )
    def execute_live(self, request: LiveRequestDescription) -> Mapping[str, Any]:
        require_live_smoke_authorization()
        return self._execute_request_live(request)

    def fetch_live(self, request: LiveRequestDescription) -> Mapping[str, Any]:
        require_live_smoke_authorization()
        return self._execute_request_live(request)

    def _execute_request_live(self, request: LiveRequestDescription) -> Mapping[str, Any]:
        raise NotImplementedError(
            "no live boundary implementation is included in this offline repository"
        )


class ProviderBoundary(LiveBoundaryAdapter):
    """Contract a live LLM screening-provider adapter must satisfy.

    This is a nominal (inheritance-based), not structural, contract:
    concrete adapters must subclass `ProviderBoundary`. `describe_request`
    must stay offline and side-effect free; subclasses implement it.
    `execute_live` is inherited from `LiveBoundaryAdapter` and always calls
    `require_live_smoke_authorization()` before delegating to
    `_execute_request_live`, and cannot be overridden (see
    `LiveBoundaryAdapter.__init_subclass__`).
    """

    name: str
    version: str
    provider: str
    model: str

    def describe_request(self, record: Mapping[str, Any]) -> LiveRequestDescription:
        raise NotImplementedError("subclasses must implement describe_request")


class ConnectorBoundary(LiveBoundaryAdapter):
    """Contract a live literature/registry connector must satisfy.

    This is a nominal (inheritance-based), not structural, contract:
    concrete connectors must subclass `ConnectorBoundary`. `describe_query`
    must stay offline and side-effect free; subclasses implement it.
    `fetch_live` is inherited from `LiveBoundaryAdapter` and always calls
    `require_live_smoke_authorization()` before delegating to
    `_execute_request_live`, and cannot be overridden (see
    `LiveBoundaryAdapter.__init_subclass__`).
    """

    name: str
    source: str

    def describe_query(self, params: Mapping[str, Any]) -> LiveRequestDescription:
        raise NotImplementedError("subclasses must implement describe_query")
