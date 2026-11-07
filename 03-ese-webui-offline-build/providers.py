"""Credential-safe live-provider registry for the ESE WebUI.

This module is a *configuration boundary*, not a client. It can name the
live providers the WebUI may one day talk to, and it can validate operator
configuration for them — nothing more. Three structural guarantees:

1. **No secret values, ever.** Credentials exist only as *references*: an
   environment-variable NAME or an OS-keychain identifier pair. The module
   contains no resolver — there is no code path that reads ``os.environ``
   values, the keychain, cookies, browser profiles, or subscription
   sessions. Leakage is impossible by construction, not by discipline.
2. **Closed input grammar, fail closed.** Unknown providers, unknown modes,
   unknown keys, unknown credential kinds, and anything shaped like secret
   material are rejected with :class:`ProviderConfigError`. Nothing is
   silently dropped, redacted, or coerced.
3. **Live access is disabled.** ``live_enabled`` defaults to ``False`` and
   ``True`` is rejected outright: enabling live execution requires a later,
   explicitly operator-authorized gate that this work package deliberately
   does not implement (see ``docs/PROVIDERS.md``).

The ``interactive_subscription`` provider represents a human driving their
own ChatGPT / Claude consumer-subscription session in their own browser.
The WebUI records only that intent; it never scrapes cookies or sessions,
never automates login, and stores no secret for this mode.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# Access modes — a closed enumeration.
ACCESS_API_KEY = "api_key"
ACCESS_LOCAL_NO_AUTH = "local_no_auth"
ACCESS_INTERACTIVE_SUBSCRIPTION = "interactive_subscription"

# Environment-variable NAMES (never values) must fit the conventional
# uppercase env grammar; OS-keychain identifiers are short bounded tokens.
# The tight grammar is the primary defense: secret material simply does not
# fit these shapes.
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_KEYCHAIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Defense in depth behind the grammar: well-known secret-material prefixes
# are rejected wherever they appear in a configuration string. This list is
# a deny-screen, not a classifier — the grammar above does the real work.
_SECRET_PREFIXES = (
    "sk-",
    "sk-ant-",
    "xoxb-",
    "xoxp-",
    "ghp_",
    "github_pat_",
    "gsk_",
    "AKIA",
    "Bearer ",
    "eyJ",  # bare JWT header
)

# Key names whose mere presence means someone is trying to hand us secret
# or session material. Rejected regardless of value.
_FORBIDDEN_KEYS = frozenset(
    {
        "value",
        "secret",
        "token",
        "password",
        "api_key",
        "apikey",
        "key_material",
        "cookie",
        "cookies",
        "session",
        "session_token",
        "browser_profile",
        "profile_path",
        "oauth",
        "refresh_token",
        "access_token",
    }
)

_INTERACTIVE_SERVICES = ("chatgpt", "claude")

# The registry itself. ``default_env_var`` documents the conventional
# environment variable NAME an operator would export; the module never
# reads its value.
_REGISTRY: dict[str, dict[str, Any]] = {
    "openai_api": {
        "label": "OpenAI API",
        "access": ACCESS_API_KEY,
        "default_env_var": "OPENAI_API_KEY",
        "notes": (
            "Metered platform.openai.com API key. Distinct from a ChatGPT "
            "consumer subscription (Plus/Pro), which is NOT an API "
            "credential and is represented by interactive_subscription."
        ),
    },
    "anthropic_api": {
        "label": "Anthropic API",
        "access": ACCESS_API_KEY,
        "default_env_var": "ANTHROPIC_API_KEY",
        "notes": (
            "Metered console.anthropic.com API key. Distinct from a Claude "
            "Pro/Max consumer subscription, which is NOT an API credential "
            "and is represented by interactive_subscription."
        ),
    },
    "ollama_local": {
        "label": "Ollama (local)",
        "access": ACCESS_LOCAL_NO_AUTH,
        "default_env_var": None,
        "notes": (
            "Locally hosted Ollama daemon in its no-auth mode. No "
            "credential of any kind is accepted for this provider."
        ),
    },
    "ollama_cloud": {
        "label": "Ollama (cloud API)",
        "access": ACCESS_API_KEY,
        "default_env_var": "OLLAMA_API_KEY",
        "notes": "Hosted ollama.com API mode; distinct from ollama_local.",
    },
    "moonshot_kimi_api": {
        "label": "Moonshot / Kimi API",
        "access": ACCESS_API_KEY,
        "default_env_var": "MOONSHOT_API_KEY",
        "notes": "Metered Moonshot (Kimi) platform API key.",
    },
    "deepseek_api": {
        "label": "DeepSeek API",
        "access": ACCESS_API_KEY,
        "default_env_var": "DEEPSEEK_API_KEY",
        "notes": "Metered DeepSeek platform API key.",
    },
    "interactive_subscription": {
        "label": "Interactive subscription (user-owned session)",
        "access": ACCESS_INTERACTIVE_SUBSCRIPTION,
        "default_env_var": None,
        "notes": (
            "The human operator drives their own ChatGPT or Claude "
            "subscription session in their own browser. The WebUI stores "
            "no secret for this mode and never scrapes cookies or session "
            "state, never imports a browser profile, and never automates "
            "login."
        ),
    },
}

PROVIDER_IDS: tuple[str, ...] = tuple(_REGISTRY)


class ProviderConfigError(ValueError):
    """Raised for any provider configuration this module refuses to hold."""


_SAFE_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


def _safe_token(value: Any) -> str:
    """Render untrusted input for an error message without echoing it.

    Exception text is a leakage surface — it gets logged. Only short,
    identifier-shaped strings are echoed back; anything else (including
    secret-shaped material) is replaced with a placeholder.
    """
    if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
        for prefix in _SECRET_PREFIXES:
            if prefix in value:
                return "<redacted>"
        return value
    return "<redacted>"


def provider_registry() -> dict[str, dict[str, Any]]:
    """Return a deep copy of the provider registry.

    Copies keep the registry immutable from the caller's side: mutating the
    returned mapping cannot corrupt subsequent reads.
    """
    return copy.deepcopy(_REGISTRY)


def _screen_string(value: str, where: str) -> None:
    """Reject any configuration string that carries secret-shaped material."""
    for prefix in _SECRET_PREFIXES:
        if prefix in value:
            raise ProviderConfigError(
                f"{where} contains secret-shaped material ({prefix!r}...); "
                "this registry holds credential references (names), never "
                "secret values"
            )


def _screen_value(value: Any, where: str, _seen: set[int] | None = None) -> None:
    """Recursively apply the secret-material deny-screen.

    Walks strings, dictionary KEYS and values, and list/tuple/set/frozenset
    members at any depth. Dictionary keys are recursed like any other value
    — a hashable container key such as ``("sk-...",)`` must not smuggle
    secret-shaped material past the screen. ``_seen`` tracks visited
    containers by identity so a tampered structure carrying a reference
    cycle terminates instead of overflowing the stack. Non-string scalars
    carry no text and are ignored.
    """
    if isinstance(value, str):
        _screen_string(value, where)
        return
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if _seen is None:
            _seen = set()
        if id(value) in _seen:
            return
        _seen.add(id(value))
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                _screen_value(sub_key, f"{where} key", _seen)
                _screen_value(sub_value, f"{where}[{_safe_token(sub_key)}]", _seen)
        else:
            for item in value:
                _screen_value(item, f"{where}[]", _seen)


def _screen_config_strings(config: dict[str, Any]) -> None:
    """Apply the recursive secret-material deny-screen to a whole config."""
    _screen_value(config, "config")


def _validate_credential_ref(ref: Any) -> dict[str, str]:
    """Validate a credential *reference* — a pointer, never a value."""
    if not isinstance(ref, dict):
        raise ProviderConfigError(
            "credential_ref must be an object naming an environment "
            "variable or a keychain item"
        )
    forbidden = _FORBIDDEN_KEYS.intersection(k.lower() for k in ref if isinstance(k, str))
    if forbidden:
        raise ProviderConfigError(
            f"credential_ref carries forbidden secret/session field(s) "
            f"{sorted(forbidden)}; only references (env name or keychain "
            "identifiers) are accepted"
        )
    kind = ref.get("kind")
    if kind == "env":
        if set(ref) != {"kind", "name"}:
            raise ProviderConfigError(
                "env credential_ref accepts exactly the keys: kind, name"
            )
        name = ref["name"]
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ProviderConfigError(
                "env credential_ref name must be an environment-variable "
                "NAME matching ^[A-Z][A-Z0-9_]{0,63}$ (the value is never "
                "read or stored)"
            )
        return {"kind": "env", "name": name}
    if kind == "keychain":
        if set(ref) != {"kind", "service", "account"}:
            raise ProviderConfigError(
                "keychain credential_ref accepts exactly the keys: kind, "
                "service, account"
            )
        service, account = ref["service"], ref["account"]
        for field_name, field in (("service", service), ("account", account)):
            if not isinstance(field, str) or not _KEYCHAIN_ID.fullmatch(field):
                raise ProviderConfigError(
                    f"keychain credential_ref {field_name} must be a short "
                    "bounded identifier (letters, digits, '.', '_', '-'; "
                    "max 64 chars)"
                )
            _screen_string(field, f"credential_ref[{field_name!r}]")
        return {"kind": "keychain", "service": service, "account": account}
    raise ProviderConfigError(
        "credential_ref kind must be 'env' or 'keychain'"
    )


def validate_provider_config(config: Any) -> dict[str, Any]:
    """Validate an operator-supplied provider configuration, fail closed.

    Returns a normalized copy. The input is never mutated; the module holds
    no state from the call. Everything outside the closed grammar —
    unknown providers, unknown keys, secret-shaped strings, live access
    without the (future) operator gate — raises :class:`ProviderConfigError`.
    """
    if not isinstance(config, dict):
        raise ProviderConfigError("provider configuration must be an object")
    config = copy.deepcopy(config)

    provider_id = config.get("provider")
    # Type-check before the membership test: an unhashable value ([] / {})
    # would otherwise raise a raw TypeError instead of the promised
    # ProviderConfigError, breaking the closed-input error contract.
    if not isinstance(provider_id, str) or provider_id not in _REGISTRY:
        raise ProviderConfigError(
            f"unknown provider {_safe_token(provider_id)}; "
            f"supported: {', '.join(PROVIDER_IDS)}"
        )
    spec = _REGISTRY[provider_id]

    allowed_keys = {"provider", "credential_ref", "live_enabled", "label"}
    if spec["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION:
        allowed_keys = {"provider", "service", "live_enabled", "label"}
    unknown = set(config) - allowed_keys
    if unknown:
        raise ProviderConfigError(
            "unsupported configuration key(s) "
            f"{sorted(_safe_token(key) for key in unknown)} "
            f"for provider {provider_id!r}"
        )

    _screen_config_strings(config)

    label = config.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip()):
        raise ProviderConfigError("label, when present, must be a non-empty string")

    live_enabled = config.get("live_enabled", False)
    if live_enabled is not False:
        raise ProviderConfigError(
            "live provider access is disabled in this build; enabling it "
            "requires the explicit operator-authorized live-test gate "
            "described in docs/PROVIDERS.md, which is not yet implemented"
        )

    credential_ref: dict[str, str] | None = None
    service: str | None = None
    if spec["access"] == ACCESS_INTERACTIVE_SUBSCRIPTION:
        service = config.get("service")
        if service not in _INTERACTIVE_SERVICES:
            raise ProviderConfigError(
                "interactive_subscription requires service to be one of: "
                + ", ".join(_INTERACTIVE_SERVICES)
            )
    elif "credential_ref" in config and config["credential_ref"] is not None:
        if spec["access"] == ACCESS_LOCAL_NO_AUTH:
            raise ProviderConfigError(
                f"provider {provider_id!r} runs in no-auth mode and accepts "
                "no credential reference"
            )
        credential_ref = _validate_credential_ref(config["credential_ref"])

    normalized: dict[str, Any] = {
        "provider": provider_id,
        "access": spec["access"],
        "live_enabled": False,
        "credential_ref": credential_ref,
    }
    if service is not None:
        normalized["service"] = service
    if label is not None:
        normalized["label"] = label
    return normalized


def public_view(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a validated config safe to render or log.

    Only reference NAMES appear (an env-var name, a keychain identifier) —
    those are non-secret by the closed grammar. As defense in depth, if a
    secret-shaped string somehow reached the config after validation, this
    raises instead of silently redacting: a leak attempt is a bug to
    surface, not to paper over.
    """
    if not isinstance(config, dict):
        raise ProviderConfigError("public_view expects a validated configuration")
    rendered = copy.deepcopy(config)
    _screen_config_strings(rendered)
    return rendered
