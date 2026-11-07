HANDOFF: wp-ui-provider-health — offline-only provider health/check boundary (round 1)

INTENT
Add the smallest useful health/test control boundary on top of the committed
provider registry (a7989a8): a deterministic, redacted provider-check
plan/result for all registered modes, classification from configuration
alone, and typed fail-closed rejection of any live-execution request or
injected network transport. Live access and credential resolution remain out
of scope and disabled.

SCOPE
Files staged (3 files, +507; providers.py deliberately byte-identical to
a7989a8 — verified `git diff a7989a8 -- providers.py` = empty):
- provider_health.py            (new, 228 lines)
- tests/test_provider_health.py (new, 256 lines — 31 deterministic offline tests)
- docs/PROVIDERS.md             (+23 lines — health-boundary section)
Out of scope (deliberate, do not flag):
- Live probes, the operator gate implementation, server/UI wiring,
  credential resolution.
- Handoff docs in docs/reviews/ and tools/codex stay untracked/unstaged.
- No commit until Osama returns VERDICT: APPROVE (his explicit gate this WP).

DESIGN (key points for review)
- Additive layering: imports ONLY providers' public API
  (validate_provider_config, public_view, provider_registry, PROVIDER_IDS,
  ACCESS_*, ProviderConfigError). public_view doubles as the redaction
  screen for every result and for to_json input.
- check_provider(config, *, live=False, transport=None):
  live is not False → LiveExecutionBlockedError; transport is not None →
  TransportInjectionError (both ProviderConfigError subclasses, checked
  BEFORE validation); then registry validation propagates rejections
  (live_enabled=True, unknown providers, secret-shaped input).
- Result = ordered steps [validate_config, classify_credential_reference,
  live_connectivity_probe]; the live step is ALWAYS blocked_offline naming
  the operator gate. Classification from config alone:
  reference_configured / unconfigured / no_auth_local / user_owned_session —
  wording explicitly never claims a secret exists or works.
- plan_registry_checks(): sorted default template per provider;
  interactive_subscription honestly reports validate_status
  "service_required" instead of pretending a service was chosen; other
  templates report "not_validated" (they are templates, not validated
  configs).
- to_json(): public_view-screen first (tampered secret-shaped result RAISES,
  never emits), then sorted-key fixed-separator JSON; no timestamps, no
  randomness → byte-deterministic.

TESTS
Command: python3.13 -m pytest tests/test_provider_health.py -q
Result: 31 passed in 0.02s
Command: python3.13 -m pytest -q   (full offline suite)
Result: 258 passed in 62.51s — zero failures (prior 227 intact)
Test-first evidence: collection ImportError captured before the module
existed (red), 31 green after; module then passed unchanged.
Coverage per the work order: configured + unconfigured API refs, ollama
local/cloud, interactive chatgpt AND claude, unknown/secret-shaped inputs,
live/transport typed rejections (parametrized truthy/callable/str/dict),
env-independence (identical results and bytes with sentinel env set vs
deleted; sentinel never in repr/JSON), canonical serialization stable across
input key reordering, purity/no-mutation, source screen (no network/process
imports, no environment reads), no resolver-shaped exports.

SCRUTINIZE (threat model, not style)
1. Find any path where a result claims a live property offline (anything
   reading as "healthy"/"verified"/working-credential).
2. Find any way to reach I/O through check_provider/plan_registry_checks/
   to_json — including via the transport/live parameters or tampered input.
3. to_json screens dicts and lists of dicts; any other type raises. Confirm
   no emit path bypasses public_view.
4. Honest uncertainty: (a) plan_registry_checks templates use
   validate_status "not_validated"/"service_required" — check the wording
   can't be misread as a failed validation; (b) `live` accepts any value and
   rejects everything but the literal False — deliberate (fail-closed), but
   confirm the asymmetry with validate's boolean-only live_enabled is
   acceptable; (c) classification for api_key modes ignores which env var
   the ref names — by design (no env reads), but say if the result should
   echo the registry's default_env_var for operator convenience.

HARD STOP
No network or provider call anywhere; no credential resolution; engine
checkout untouched; nothing committed — commit is gated on Osama's explicit
VERDICT: APPROVE.
