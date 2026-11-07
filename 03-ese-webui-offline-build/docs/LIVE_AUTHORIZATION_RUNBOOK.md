# Live connector authorization runbook

The WebUI is currently safe to run offline. This runbook defines the exact
boundary for a later live test; reading it does not authorize a request.

## Before any live test

1. Choose exactly one connector and one bounded operation.
2. Confirm the target account, institution, subscription, and permitted use.
3. Handle login, two-factor authentication, institutional confirmation, and
   license prompts yourself.
4. Keep secrets out of chat, source files, run artifacts, screenshots, and
   operator notes. Use a locally named environment variable or user-owned
   keychain reference only.
5. Freeze the citation/protocol identity and record the expected DOI, PMID,
   title, journal, and year.
6. Confirm that the expected result is metadata/entitlement status or a
   manually observed article—not an unverified treatment conclusion.

## Provider modes

- OpenAI and Anthropic API modes use a caller-injected resolver and transport
  behind `provider-health-v1`. The WebUI currently issues only the offline
  gate and call plan.
- ChatGPT and Claude consumer subscriptions remain manual browser sessions.
  The system does not scrape cookies, automate login, or automate 2FA.
- Ollama local/cloud, Moonshot/Kimi, and DeepSeek use the same reference-only
  API control contract. Local Ollama no-auth mode must not receive a key.

## Institutional access modes

- Google Scholar, EZproxy, publisher pages, and interlibrary loan are manual
  operator routes.
- Scopus and Embase can build an identity-bound metadata/entitlement probe
  plan using a named credential reference. The probe does not assert full
  text; a later result must be identity-checked.
- A manual access record may set `verified_full_text: true` only after the
  operator supplies matching DOI/PMID/title evidence and an HTTPS source URL.
  `license_status` remains `not_assessed` until separately reviewed.

## Stop conditions

Stop and ask the user to intervene if the flow requests a secret in chat,
institutional login, 2FA, license acceptance, purchase, subscription change,
unbounded search, a browser profile, a cookie/session export, or a source whose
identity cannot be matched to the frozen citation.

No live connector should be called from the WebUI until a separate work
package wires one explicitly authorized resolver and transport and adds a
bounded live acceptance test.
