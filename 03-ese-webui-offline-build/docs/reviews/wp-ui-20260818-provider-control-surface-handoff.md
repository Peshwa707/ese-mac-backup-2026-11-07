# Crosstalk handoff: read-only provider control surface

- Work package: `wp-ui-20260818-provider-control-surface`
- Branch: `codex/webui-offline-build`
- Scope: `server.py`, `static/index.html`, `static/app.js`, `tests/test_provider_endpoints.py`, `docs/PROVIDERS.md`, `README.md`
- Out of scope: original ESE checkout, credentials, environment/keychain reads, network calls, gate issuance, transport, resolver, live adapters, subscription automation, and run-store writes

## Intent

Expose the existing credential-safe provider modules through a local,
stateless, read-only operator surface. The panel covers OpenAI, Anthropic,
Ollama local/cloud, Moonshot/Kimi, DeepSeek, and manual ChatGPT/Claude
subscription modes.

## Invariants reviewed

- Configurations contain credential references only; no secret value is accepted or echoed.
- `live_enabled` remains false and live connectivity remains `blocked_offline`.
- API manifests expose structural auth posture, never header values.
- Subscription manifests contain no HTTP section and the UI has no automation action.
- Endpoints do not touch the run store or expose gate/call-plan/execution seams.
- Equal requests produce deterministic response bytes.

## Verification

```text
python3.13 -m pytest -q tests/test_provider_endpoints.py
13 passed

python3.13 -m pytest -q
541 passed in 69.22s
```

## Review request

Please inspect the staged diff and tests for contract violations, especially
secret echo, unintended I/O, endpoint exception handling, and any accidental
subscription automation. Return a structured `VERDICT: APPROVE` or
`VERDICT: CHANGES` with file/line-specific findings. Commit is gated on
approval.

## Independent review outcome

PAI was asked three bounded times but did not return a review-result. Direct
Claude CLI review was unavailable because the local CLI is not authenticated.
An independent read-only Codex subprocess then reviewed the staged diff and
returned:

```text
VERDICT: APPROVE
No blocker findings.
```

It specifically confirmed read-only routes, no credential/store/gate/transport
I/O, manual-only subscriptions, and coverage of determinism, secret non-echo,
blocked live access, and no artifact mutation.
