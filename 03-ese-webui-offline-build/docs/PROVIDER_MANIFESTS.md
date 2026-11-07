# Provider request manifests (`provider_manifest.py`)

**This package is preparation for future, separately reviewed live
adapters. It does not authorize network access.** The operator gate
(`provider_gate.py`, see `docs/PROVIDERS.md`) remains the required
per-provider/per-run authorization for any future live call — including its
carried-forward constraint that a gate's content hash is integrity, not
proof of issuance, so a live executor needs an authenticated issuer
boundary.

## What a manifest is

`build_health_request(config)` emits a canonical, deterministic
*declaration* of what a health check would involve for one validated
provider configuration:

| mode | protocol family | shape |
|------|-----------------|-------|
| `openai_api`, `anthropic_api`, `ollama_cloud`, `moonshot_kimi_api`, `deepseek_api` | `http` | `GET` + route + host, auth posture `{scheme: bearer, header: Authorization}` |
| `ollama_local` | `http` | `GET /api/tags` on the local daemon, auth `{scheme: none}` |
| `interactive_subscription` (`chatgpt` / `claude`) | `manual_session` | **no HTTP section at all** — a manual-operator instruction; an automated request is unrepresentable |

- **Credential metadata is references only**: the validated ref (env-var
  name / keychain identifiers) and the registry's default env-var *name*,
  plus a posture (`reference_configured` / `unconfigured` /
  `none_required` / `user_owned_session`). Never secret values,
  environment contents, cookies, browser profiles, or subscription tokens.
  Auth posture is structure, never a header-value template.
- **Binding and identity**: each manifest binds `config_sha256` (the same
  validated-config fingerprint the operator gate uses) and carries a
  content-derived `manifest_id`; tampering and config drift are detected by
  recomputation (`ManifestTamperError` / `ManifestStaleError`). No clocks,
  no randomness — equal inputs give byte-identical manifests.
- **Routes and hosts are declarative targets.** They have not been
  contacted; verifying them against the real services is the live-adapter
  work package's job.

## The blocked seams

- `CredentialResolver` (abstract): future resolution at the execution
  edge. The only implementation in this module, `OfflineCredentialResolver`,
  validates the *reference* through the registry and then always raises
  `CredentialResolutionBlockedError` — it cannot read env/keychain and
  never returns a secret.
- `Transport` (abstract): the manifest module does not implement a live
  transport. The separately reviewed `provider_executor` module offers
  optional, caller-injected `EnvironmentCredentialResolver` and bounded
  `UrllibTransport` capabilities behind its versioned authorization and
  gate-burn hook; this manifest module remains I/O-free.
- `execute_manifest(manifest, config)` verifies structure, tamper,
  version, provider binding, and config fingerprint — then raises
  `LiveProviderExecutionBlockedError` even for a fully valid manifest.
  Injected transports or resolvers and any `live=` request are refused
  with the same typed error, checked first. Execution is stateless: every
  repeated attempt is refused identically.

## Subscription modes stay manual

ChatGPT/Claude **API** modes (`openai_api` / `anthropic_api`, metered
platform keys) are distinct from user-owned ChatGPT/Claude **subscription
sessions** (`interactive_subscription`). Subscription manifests are
manual-operator-only: the human performs the check in their own session;
the WebUI never automates, scripts, logs into, or reads that session.
