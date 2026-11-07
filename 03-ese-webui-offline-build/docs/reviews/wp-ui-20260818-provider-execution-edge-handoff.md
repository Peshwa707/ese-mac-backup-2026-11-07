# Provider execution-edge increment

## Intent

Add a versioned, credential-safe execution edge for one health-check fixture
without making the WebUI invoke it automatically. A user-owned runtime may
inject a resolver and transport only after explicit authorization.

## Contract

- `provider-health-v1` is the fixed execution fixture.
- Every invocation revalidates the canonical plan, provider configuration,
  gate binding, and exact authorization statement inside the execution hook.
- The caller-owned gate burns before credential resolution or transport.
- `EnvironmentCredentialResolver` and bounded `UrllibTransport` are optional
  explicit runtime capabilities; they are not constructed by the WebUI.
- `UrllibTransport` uses a no-proxy, no-redirect opener, a fixed host allowlist,
  bounded JSON responses, and rejects cookies, control characters, and other
  noncanonical request headers.
- Results contain only a normalized response summary. Request headers and
  credential values never appear in returned artifacts or failure messages.
- API/local modes are supported through the injected runtime. User-owned
  ChatGPT/Claude sessions remain manual and reject automated execution.

## Verification

- Full suite: `575 passed`.
- Execution/gate/call-plan/manifest tests: `214 passed`.
- `node --check static/app.js`: passed.
- `python3.13 -m compileall`: passed.
- No user credentials were entered or read and no live provider call was made
  by Codex. HTTP tests use a monkeypatched in-memory `urlopen` fixture.

## Crosstalk

Three bounded PAI retries for this execution-edge decision timed out:
`rq-ce434ef01e0f`, `rq-06a8e9b4849f`, and `rq-86ad9e819b8f`. Work continued
under the Codex-owned fallback after the required retries; no PAI-owned files
or processes were modified.

## Deferred

The WebUI does not expose an automatic live-execution button in this
increment. Keychain resolution, provider-specific request/auth refinements,
live calls, subscription automation, and institutional full-text access are
separate reviewed increments.
