# Work package handoff: offline full-text access routing

Date: 2026-08-18

## Intent

Add a small, deterministic WebUI contract that helps an operator choose a
lawful route toward article access while keeping discovery, entitlement,
retrieval, and identity verification distinct.

## Scope

- `full_text_access.py`: connector catalog, citation preservation, DOI/PMID
  normalization, deterministic route plan, and offline identity comparison.
- `full_text_config.py`: reference-only Scopus/Embase/EZproxy access-profile
  validation; no secret resolution or browser/session access.
- `full_text_probe.py`: identity-bound, caller-injected Scopus/Embase
  metadata/entitlement probe contract; no default resolver/transport and no
  WebUI execution path.
- `manual_access_record.py`: user-owned access record with strict identity
  matching and HTTPS provenance; no fetching or session access.
- `server.py`: `GET /api/full-text/connectors` and
  `POST /api/full-text/plan`, plus access-profile registry and validation
  endpoints and the offline `probe-plan` endpoint.
- `server.py` also exposes the stateless `manual-record` endpoint; it writes no
  run artifact or downloaded article.
- `static/index.html` / `static/app.js`: minimal route-planning panel.
- No network connector, institutional login, cookie/session access, paywall
  bypass, download, extraction, or run artifact creation.

## Safety and provenance contract

- The original citation is preserved in the returned plan.
- `plan_id` is a sha256 of the canonical plan body.
- Every route has `verified_full_text: false`.
- Scholar, Scopus, Embase, EZproxy, publisher, ILL, and manual-browser routes
  explicitly describe what remains for the operator to verify.
- Secret-shaped citation input is rejected without echo.
- The endpoints are stateless and do not write run artifacts.
- A validated profile changes only `configuration_state`; it never changes
  `verified_full_text`, which remains false until a future retrieval and
  identity gate.
- Probe execution, if later authorized, burns a caller-owned single-use
  attempt before resolver/transport, returns only status/hash metadata, and
  retains the false full-text claim ceiling.
- Manual records can set `verified_full_text` only after matching all available
  citation identity fields; their `license_status` remains `not_assessed`.

## Verification

- Combined connector/config/probe/manual-record tests: 50 passed.
- `node --check static/app.js`: passed.
- Full repository suite: 627 passed.
- Independent Claude review: `VERDICT: APPROVE`; non-blocking polish items
  were fixed for version-constant reuse, candidate identity typing, empty
  connector selection, and a non-vacuous artifact assertion.
- The later institutional-profile diff was sent to the authenticated Claude
  CLI twice with bounded retries; both calls stalled after local permission
  notices and returned no verdict. This is recorded as an unavailable review,
  not as approval. Local focused tests remain the evidence for this follow-on
  increment.
- Follow-on local hardening rejects stale profile versions and unexpected
  profile fields, and treats blank candidate identity text as absent.
- A source-level test confirms the full-text routing/configuration modules have
  no URL client, environment read, keychain, subprocess, or browser-session
  path.

## Open boundary

The next retrieval increment, if authorized, must separately specify one
connector, its terms/entitlement boundary, user-owned authentication flow,
bounded response handling, and DOI/PMID/title identity verification. It must
not infer full text from a search hit or an entitlement link.
