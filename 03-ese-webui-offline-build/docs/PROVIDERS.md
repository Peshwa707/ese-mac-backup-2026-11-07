# Live-provider registry boundary

`providers.py` is a **configuration boundary, not a client**. It lets the
WebUI name and validate the live providers it may one day talk to, while
making live access and credential exposure structurally impossible today.

## Supported provider modes

| id | access mode | credential reference | distinction |
|----|-------------|----------------------|-------------|
| `openai_api` | `api_key` | `OPENAI_API_KEY` (name only) | Metered platform API — **not** a ChatGPT consumer subscription |
| `anthropic_api` | `api_key` | `ANTHROPIC_API_KEY` (name only) | Metered platform API — **not** a Claude Pro/Max subscription |
| `ollama_local` | `local_no_auth` | none accepted | Local daemon, no-auth mode |
| `ollama_cloud` | `api_key` | `OLLAMA_API_KEY` (name only) | Hosted API mode, distinct from local |
| `moonshot_kimi_api` | `api_key` | `MOONSHOT_API_KEY` (name only) | Metered Moonshot/Kimi API |
| `deepseek_api` | `api_key` | `DEEPSEEK_API_KEY` (name only) | Metered DeepSeek API |
| `interactive_subscription` | `interactive_subscription` | none accepted | The human drives their own ChatGPT/Claude session; `service` is `chatgpt` or `claude` |

## The boundary

1. **Credential references only.** A credential is represented as an
   environment-variable NAME (`{"kind": "env", "name": "OPENAI_API_KEY"}`)
   or an OS-keychain identifier pair (`{"kind": "keychain", "service": …,
   "account": …}`). The module contains **no resolver**: no code path reads
   environment values, the keychain, cookies, browser profiles, or
   subscription sessions. Secret values are never accepted, persisted,
   printed, or tested with.
2. **Fail closed.** Unknown providers, unknown modes/services, unknown or
   forbidden keys (`value`, `token`, `cookie`, `session`, …), malformed
   references, and strings carrying well-known secret prefixes (`sk-`,
   `xoxb-`, `AKIA`, `Bearer `, …) are rejected with `ProviderConfigError`.
   The deny-screen deliberately over-rejects (any substring match) —
   fail-closed beats convenience here. Nothing is silently redacted or
   coerced; `public_view()` *raises* if a secret-shaped string would ever
   escape, because that would be a bug to surface, not paper over.
3. **Live access is disabled.** Every validated configuration has
   `live_enabled: false`. Passing `true` is rejected. No network calls, no
   credential loading, no subscription-session invocation, no provider
   settings changes happen anywhere in this package.
4. **`interactive_subscription` is intent, not automation.** It records
   that the operator will use their own consumer-subscription session
   (ChatGPT or Claude) manually. No cookie/session scraping, no browser
   profile import, no automatic OAuth or login — ever.

## Offline health/check boundary (`provider_health.py`)

`check_provider(config)` returns a deterministic, redacted check plan/result
for any registered provider mode. It is a plan that refuses to lie:

- **Config steps run offline** — validation via the registry, plus a
  credential-posture classification derived purely from the configuration
  object (`reference_configured` / `unconfigured` / `no_auth_local` /
  `user_owned_session`). Nothing reads environment values, the keychain,
  cookies, browser profiles, or subscription sessions —
  `reference_configured` means a *name* is present, never that a secret
  exists or works.
- **Live steps refuse to run** — every result's `live_connectivity_probe`
  step reports `blocked_offline` pending the operator gate below. Passing
  `live=<anything but False>` raises `LiveExecutionBlockedError`; injecting
  a `transport` raises `TransportInjectionError` (both typed
  `ProviderConfigError` subclasses). The module performs no I/O at all.
- **Deterministic, redacted serialization** — `to_json()` re-screens through
  `public_view` (a tampered result raises rather than leaking) and emits
  sorted-key JSON with no timestamps or randomness, so equal inputs produce
  byte-identical output. `plan_registry_checks()` gives the sorted default
  template plan for all providers.

## WebUI control surface

The local WebUI exposes six stateless provider routes over these pure
modules:

- `GET /api/providers` lists the registry and the default offline check plan.
- `POST /api/providers/validate` validates one configuration and returns its
  public reference-only view.
- `POST /api/providers/health-plan` returns the deterministic offline health
  result; its live connectivity step remains `blocked_offline`.
- `POST /api/providers/manifest` returns the declarative health-request
  manifest without contacting its host.
- `POST /api/providers/gate` issues one deterministic, per-run operator gate
  only after the request carries an explicit confirmation statement naming
  the provider and `health_check` action. In the local UI this is paired with
  an explicit checkbox; it is an operator-intent guard, not proof of identity
  or a credential. The endpoint does not burn or execute the gate.
- `POST /api/providers/call-plan` rebuilds the manifest and composes a
  gate-bound call plan. It never resolves a credential, burns the gate, or
  sends a request; the returned plan explicitly says it is not executable in
  this build.

The Providers panel sends only provider configuration, credential-reference
names, ChatGPT/Claude service labels, the selected run id, and an explicit
operator acknowledgment. It does not read environment variables or
keychains, write the run store, or create a transport/execution path.
`interactive_subscription` is rendered as a manual operator instruction with
no automation action. Equal requests produce deterministic responses;
malformed or secret-shaped requests fail closed.

## The operator-authorized live-test gate (`provider_gate.py`)

The gate *artifact* now exists; live execution still does not. The registry
continues to reject `live_enabled: true` unchanged — the gate is the future
execution seam's authorization record, not a bypass of anything above.

- `issue_gate(config, run_id=…, action=…, operator_ack=…)` records an
  explicit authorization for exactly **one provider, one run, one bounded
  action** (`health_check` is the only supported action). The
  acknowledgment must be `{"confirmed": true, "statement": …}` where the
  statement names both the provider id and the action — blanket
  acknowledgments are rejected, and no global/wildcard gate is expressible.
- The artifact is **clock-free and deterministic**: it binds the sha256
  fingerprint of the validated configuration's canonical redacted JSON, and
  its own `gate_id` is the sha256 of its canonical body — equal inputs give
  byte-identical gates; any tamper is detected by recomputation. Staleness
  is structural (config drift or unknown version), and replay is prevented
  by a caller-owned single-use ledger: a gate **burns on its first
  execution attempt**. The WebUI gate endpoint is stateless and only issues
  the deterministic artifact; single-use enforcement belongs to that future
  execution ledger, not to repeated offline planning requests.
- `execute_gated_action(...)` verifies everything — transport injection,
  structure, tamper, acknowledgment, run/action/provider binding, config
  fingerprint, reuse — and then raises `LiveExecutionNotImplementedError`
  **even for a fully valid gate**. This package can prove an authorization;
  it structurally cannot act on one. Credentials remain references
  throughout; nothing reads environment values, keychains, cookies, browser
  profiles, or subscription sessions.
- Interactive ChatGPT/Claude gates carry an explicit note that any future
  execution is manual and operator-driven — never automated.
- Actual live execution (with credential resolution at the execution edge,
  scoped to the gated call, never persisted or logged) remains a future,
  separately reviewed work package behind this same seam.

## Gate-bound call plans and response normalization (`provider_call_plan.py`, `provider_response.py`)

**Still preparation, not a live adapter.** These modules compose and
normalize; neither can execute. Actual credential resolution and live
provider calls require a future, separately reviewed live-adapter work
package, **explicit user authorization**, and the existing operator gate —
nothing in this package weakens or bypasses any of that.

- `build_call_plan(config, manifest, gate, run_id=…, action=…)` composes
  the three existing artifacts into the one declarative unit a future live
  adapter would execute. It verifies gate integrity (content hash, version,
  acknowledgment semantics) and the full provider/run/action/configuration
  binding of both gate and manifest — **without burning the gate**:
  planning is read-only and stateless (no single-use ledger is touched), so
  a planned gate remains fresh for the burning execution seam in
  `provider_gate`.
- **API-mode plans** (`openai_api`, `anthropic_api`, `ollama_local`,
  `ollama_cloud`, `moonshot_kimi_api`, `deepseek_api`) carry exactly the
  manifest's request shape (method/route/host/structural auth posture), the
  provider/run/action/gate/manifest identifiers, and an explicit
  future-executor requirement (`executable_in_this_build: false`,
  `operator_gate_required: true`). No secret values, resolved credentials,
  cookies, browser profiles, or subscription tokens appear anywhere;
  `plan_id` is the sha256 of the canonical body, so tamper is detectable by
  recomputation and equal inputs yield byte-identical plans.
- **Subscription plans** (ChatGPT / Claude `interactive_subscription`) are
  manual-operator-only and make automated execution *unrepresentable*:
  the plan contains no HTTP section, no request, and no transport surface —
  its executor is the human operator, full stop.
- `execute_call_plan(plan, config, gate, …)` is the blocked execution seam.
  It requires the operator gate itself (a plan is gate-*bound*) and
  enforces **canonical-rebuild equality**: the presented plan must equal a
  fresh `build_call_plan` over the config, a rebuilt manifest, and the
  gate, so a hash-self-consistent forgery (foreign request fields, flipped
  execution flags, forged identifiers) is rejected as invalid — never
  treated as verified. An injected transport, an injected resolver,
  `live=true`, and even a fully valid, fully bound plan all terminate in
  `LiveProviderExecutionBlockedError`. Stateless, clock-free, I/O-free,
  and non-burning — burn-on-attempt remains `provider_gate`'s contract at
  the real (future) execution edge.
- `normalize_health_response(config, response)` normalizes **bounded,
  synthetic** health responses only (it is the shape contract for the
  future adapter, exercised today with fixtures). Each provider mode
  accepts exactly one validated response family (OpenAI-compatible /
  Anthropic / Ollama model lists on 200; provider-matched auth-error bodies
  on 401/403 for `api_key` modes). The summary is canonical and redacted —
  provider, operation, status/classification, a safe model count or the
  manual result, and a sha256 of the screened response — **never the raw
  response**. Secret-shaped responses (e.g. an auth error echoing a key)
  are rejected outright without echoing the secret. Subscription results
  remain manual operator records; an automated HTTP-shaped response for a
  subscription mode raises `AutomatedSubscriptionResponseError`.

## Authorized execution-edge contract (`provider_executor.py`)

The next boundary is a capability contract, not an automatic network path.
`execute_authorized_health_check()` supports the six API/local modes through
caller-injected `CredentialResolver` and `Transport` objects only. It has no
default resolver, no default transport, and no environment, keychain, browser,
or subprocess access.

- Every invocation revalidates the canonical plan, provider configuration,
  gate binding, and an exact authorization object containing `confirmed`, the
  fixed fixture id `provider-health-v1`, and a statement naming the provider,
  `health_check`, and that fixture. Validation occurs inside the execution
  hook, so direct construction of an authorization or plan cannot bypass it.
- After those checks and runtime-dependency checks, the caller-owned gate is
  burned before credential resolution or transport invocation. Resolver and
  transport failures are redacted; the returned artifact contains only the
  fixture id and normalized response summary, never request headers or a
  credential value.
- ChatGPT/Claude user-owned subscription sessions remain manual and raise a
  typed `ManualSubscriptionRequiredError` at this automated edge. The UI is
  intentionally not wired to trigger this function in this increment.
- The current tests use only in-memory resolver/transport fixtures. No live
  provider call or credential resolution is part of Codex verification.
