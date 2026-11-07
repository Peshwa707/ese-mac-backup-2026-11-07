HANDOFF: wp-ui-provider-manifest — request manifests + blocked resolver/transport seams (round 1)

INTENT
Prepare — and only prepare — the shapes a future live adapter needs:
canonical per-provider health-request manifests bound to validated-config
hashes, a typed credential-resolver interface whose only implementation
always refuses, and a typed transport seam whose execution function blocks
everything. No network access is authorized or possible; the operator gate
(770485a) remains the required authorization for any future live call.

SCOPE
Files staged (3 NEW files, +747, zero modifications — verified
`git diff 770485a -- providers.py provider_health.py provider_gate.py
docs/PROVIDERS.md` = empty):
- provider_manifest.py            (new, 308 lines)
- tests/test_provider_manifest.py (new, 379 lines — 47 deterministic offline tests)
- docs/PROVIDER_MANIFESTS.md      (new, 60 lines)
Out of scope (deliberate, do not flag):
- Live I/O, credential resolution, route/host live verification (routes
  are DECLARATIVE targets — documented; the live-adapter WP must verify
  them against the real services).
- Gate integration into execute_manifest (authority stays with
  provider_gate; manifests carry no consumable authority — separation of
  declaration/authority/refusal is the design).
- Handoff docs + tools/codex stay untracked; no commit until Osama's
  VERDICT: APPROVE.

DESIGN (key points for review)
- build_health_request/build_request(config, operation): registry
  validation first (rejects live_enabled, unknown providers, secret-shaped
  input); SUPPORTED_OPERATIONS == ("health_check",).
- API modes → protocol_family http: GET /v1/models on api.openai.com /
  api.anthropic.com / api.moonshot.ai / api.deepseek.com; GET /api/tags on
  ollama.com; ollama_local → GET /api/tags on 127.0.0.1:11434 with auth
  {scheme: none}. API auth posture is STRUCTURE — {scheme: bearer, header:
  Authorization} — never a header-value template (the registry's own
  screen bans the "Bearer " literal; the safety layer shaped the API).
- interactive_subscription (chatgpt/claude) → protocol_family
  manual_session with NO http key at all: an automated request is
  unrepresentable, requires.network false, manual_instruction states the
  WebUI never automates/scripts/logs into/reads the session.
- credential block: posture (reference_configured/unconfigured/
  none_required/user_owned_session) + validated ref + registry
  default_env_var NAME. References only, proven by sentinel
  env-set-vs-deleted byte-equality tests.
- Binding/identity: config_sha256 = provider_gate.config_fingerprint
  (shared with the gate); manifest_id = sha256(canonical body); tamper →
  ManifestTamperError, drift → ManifestStaleError, unknown version →
  stale. Clock-free, no randomness (source-screened).
- OfflineCredentialResolver.resolve: requires a dict, then REUSES the
  registry's full reference validation (via a scratch config), then always
  raises CredentialResolutionBlockedError. No env/keychain path exists.
- execute_manifest(manifest, config, resolver=None, transport=None,
  live=False): injected transport → blocked; injected resolver → blocked;
  live non-False → blocked; then structure/version/tamper/provider/
  fingerprint checks; then LiveProviderExecutionBlockedError EVEN for a
  valid manifest. Stateless — replay refusal is identical every attempt.
- All five new errors subclass ProviderConfigError.

TESTS
Command: python3.13 -m pytest tests/test_provider_manifest.py -q
Result: 47 passed in 0.02s
Command: python3.13 -m pytest -q   (full offline suite)
Result: 362 passed in 62.43s — zero failures (prior 315 intact)
Test-first evidence: collection ImportError captured before the module
existed (red); 47 green after.
Coverage: all seven modes; API-vs-subscription distinction (incl. distinct
manifest_ids); no "Bearer "/"sk-" in any rendered manifest; ollama
local-vs-cloud; credential reference metadata; env-independence both
directions; content-derived manifest_id recomputed independently in test;
key-order determinism; canonical JSON; build-time rejection (7 malformed
config shapes); unsupported operations; resolver blocked-for-valid /
rejected-for-malformed (6 shapes) / never-returns-value-with-env-set;
typed ABC checks (inspect.isabstract); valid-manifest execution blocked;
3x replay refusal; injected transport (4 shapes, incl. a rogue Transport
subclass asserting send() is never reached); injected resolver; live
truthy; 5 bogus manifest shapes; 4 tamper fields; config drift; version
staleness; error hierarchy; purity; no-I/O source screen (also bans
time/random/date* imports).

SCRUTINIZE (threat model, not style)
1. Find any way a manifest, resolver, or the execution path can cause I/O
   or read env/keychain/session state — including via the scratch-config
   reuse inside OfflineCredentialResolver.resolve.
2. Subscription boundary: confirm an automated request truly cannot be
   represented (no http key, network false) and that no code path
   upgrades a manual_session manifest into an http one.
3. Honest uncertainty: (a) routes/hosts are from my knowledge, not
   verified against live services — declared as declarative targets in
   docs; flag if you want them marked more loudly in the manifest itself
   (e.g. a "declared_unverified" field); (b) execute_manifest does not
   require an operator gate — deliberate separation (manifests carry no
   authority; the gate seam already blocks independently) — say if you
   think the execution seam should also demand a gate now rather than in
   the adapter WP; (c) OfflineCredentialResolver validates refs against a
   scratch "openai_api" config — semantically odd but reuses the full
   grammar/screen; flag if you'd rather see a dedicated public ref
   validator (would require modifying providers.py, which this WP forbids).
4. Confirm docs/PROVIDER_MANIFESTS.md cannot be read as authorizing
   network access, and that the gate-remains-required + issuer-boundary
   carry-forward is stated.

HARD STOP
No network or provider call anywhere; no credential resolution; engine
checkout and all previously committed files untouched (all-new-files
diff); nothing committed — commit gated on Osama's VERDICT: APPROVE.
