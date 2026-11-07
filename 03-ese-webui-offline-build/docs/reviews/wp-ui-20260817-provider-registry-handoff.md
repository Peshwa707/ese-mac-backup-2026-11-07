HANDOFF: wp-ui-provider-registry — credential-safe live-provider registry module (offline, fail-closed)

INTENT
Add a provider registry/configuration module (`providers.py`) covering OpenAI
API, Anthropic API, Ollama local (no-auth) and cloud/API modes, Moonshot/Kimi
API, DeepSeek API, and an explicit `interactive_subscription` mode for
user-owned ChatGPT/Claude sessions. Credentials exist ONLY as references
(env-var names / OS-keychain identifiers); live access is disabled and
`live_enabled: true` is rejected pending a future, explicitly
operator-authorized live-test gate. Pure configuration behavior — no network,
no credential resolution, no subscription automation.

SCOPE
Files touched (staged, 4 files, +803):
- providers.py                (new, 344 lines — the registry + validation)
- tests/test_providers.py     (new, 396 lines — 61 deterministic offline tests)
- docs/PROVIDERS.md           (new, 58 lines — boundary + future gate doc)
- README.md                   (+5 lines — pointer; offline boundary text unchanged)
Out of scope (deliberate, do not flag):
- Server/UI wiring of the registry (later WP; server.py untouched).
- The operator live-test gate implementation (documented only; rejection of
  `live_enabled: true` IS the current gate).
- A socket-blocking pytest fixture for the whole suite (module-level
  no-network is asserted by a source-screen test instead).
- tools/pai_loop.py, bridge.py, store.py, engine repo: all untouched.
- This handoff file itself is intentionally NOT staged.

DIFF
Staged stat:
 README.md               |   5 +
 docs/PROVIDERS.md       |  58 +++++++
 providers.py            | 344 +++++++++++++++++++++++++++++++++++++++++
 tests/test_providers.py | 396 ++++++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 803 insertions(+)
Open the staged files directly (`git diff --staged`) — key design points:
- Closed grammar: env names must match ^[A-Z][A-Z0-9_]{0,63}$; keychain
  service/account must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$.
- No resolver exists anywhere: no code path reads os.environ values,
  keychain, cookies, profiles, or sessions. Safety by structural absence.
- _SECRET_PREFIXES deny-screen (sk-, sk-ant-, xoxb-, xoxp-, ghp_,
  github_pat_, gsk_, AKIA, "Bearer ", eyJ) applied to every config string;
  substring match, deliberately over-rejecting (fail closed).
- _FORBIDDEN_KEYS (value/secret/token/password/api_key/cookie/session/
  browser_profile/oauth/refresh_token/access_token/...) rejected regardless
  of value.
- validate_provider_config: deep-copies input (purity), rejects unknown
  providers/keys/kinds/services, forces live_enabled False, rejects True
  and any operator_live_gate key.
- interactive_subscription: service enum {chatgpt, claude} only; zero
  credential/cookie/session fields accepted.
- public_view(): raises (never silently redacts) if a secret-shaped string
  would escape serialization.
- _safe_token(): error messages never echo offending input (exception text
  is a leakage surface); non-identifier-shaped input renders as <redacted>.

TESTS
Command: python3.13 -m pytest tests/test_providers.py -q
Result: 61 passed in 0.07s
Command: python3.13 -m pytest -q   (full suite)
Result: 214 passed in 67.20s — zero failures, prior suite untouched and green
Test-first evidence:
- Before providers.py existed: "ImportError while importing test module
  .../tests/test_providers.py" (collection error, red).
- test_error_messages_never_echo_secret_shaped_input observed FAILING
  (1 failed) before the _safe_token fix, then green.
Determinism: tests are pure-dict; the only monkeypatch.setenv exists to
PROVE env values never appear in output (sentinel never leaks).

SCRUTINIZE (adversarial questions — the threat model, not style)
1. Find any path that yields a validated config with live_enabled != False.
2. Find any path where a stored credential reference is dereferenced (env
   value read, keychain read, any I/O). There should be none — the module
   should contain no resolver at all.
3. Find any output surface (return values, repr, exception text) that can
   emit secret material or echo rejected input.
4. Find any place where an unparseable/unknown value defaults to permissive
   instead of raising ProviderConfigError.
5. Honest uncertainty — where I'm least sure:
   a. The substring-based _SECRET_PREFIXES screen over-rejects (e.g. a
      keychain service containing "task-" trips the "sk-" prefix as a
      substring: "task-manager" is rejected). I chose fail-closed over
      convenience and documented it in docs/PROVIDERS.md. Confirm you agree
      this is the right trade, or propose anchored matching.
   b. "eyJ" (bare JWT prefix) in the deny list is aggressive; identifier
      grammar makes it near-unreachable, but check for interactions.
   c. allowed "label" key is screened but otherwise free-form (≤ no length
      cap). Should it get a length bound?
6. Confirm nothing in the diff weakens the repo's existing offline
   boundary (README boundary text, server.py untouched).

HARD STOP
No live APIs, credentials, cookies, subscriptions, OAuth, network calls, or
engine-repository changes were used or introduced. Nothing is committed;
commit is blocked pending your independent verdict per
.dual-review/PROTOCOL.md.
