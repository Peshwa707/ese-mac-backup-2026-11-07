# Provider gate and call-plan WebUI increment

## Intent

Expose the existing per-provider/per-run operator gate and gate-bound call
plan through the local WebUI without adding credential resolution, live
transport, subscription automation, or run-artifact writes.

## Files in this increment

- `server.py`
- `static/index.html`
- `static/app.js`
- `tests/test_provider_endpoints.py`
- `README.md`
- `docs/PROVIDERS.md`

## Behavior

- `POST /api/providers/gate` accepts exactly the configuration, run id,
  `health_check` action, and explicit operator acknowledgment; it returns a
  deterministic gate artifact and never burns or executes it.
- `POST /api/providers/call-plan` accepts exactly the configuration, gate,
  run id, and action; it rebuilds the manifest and returns a deterministic,
  non-executable call plan.
- Unknown request fields, secret-shaped values, malformed gates, mismatched
  provider/config/run bindings, and unsupported actions fail closed without
  echoing secret-shaped input.
- The UI clears stale gate state when the run, provider, credential-reference
  name, or subscription service changes. Subscription plans remain manual and
  contain no automated request.

## Verification

- Full suite: `552 passed`.
- Provider endpoint contract: `24 passed`.
- `node --check static/app.js`: passed.
- `python3.13 -m compileall`: passed.
- `git diff --check`: passed.
- No credentials were entered or read; no live provider or network call was
  made by Codex.

## Crosstalk record

Three bounded PAI requests were sent through AgentTalk and timed out without
responses: `rq-58a45602fb6e`, `rq-2cd5fbf6ddc7`, and `rq-864e24a146c0`.
After those retries, the work proceeded under the documented fallback rather
than waiting indefinitely. An authenticated Claude CLI performed independent
read-only diff reviews. The final review returned `VERDICT:
APPROVE-WITH-NITS` with no blocker or major findings. Accepted residual nits:
the run id is an opaque validated binding rather than a store-existence check,
and single-use enforcement belongs to the future execution ledger, not this
stateless planning endpoint.

## Deferred

Actual credential resolution, live HTTP adapters, response execution, and
ChatGPT/Claude subscription automation remain a later separately reviewed
increment requiring explicit user authorization.
