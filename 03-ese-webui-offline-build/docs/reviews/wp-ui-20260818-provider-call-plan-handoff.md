HANDOFF: wp-ui-provider-call-plan — gate-bound call planning + health-response
normalization (round 1)

INTENT
Compose the three committed artifacts (validated config, request manifest
313f6df, operator gate 770485a) into the one declarative unit a future live
adapter would execute — a gate-bound call plan — and add the response side
of the same boundary: normalization of bounded synthetic health responses
into canonical redacted summaries. Live access, credential resolution, and
network remain out of scope and structurally blocked.

SCOPE
Files staged (5 files, +2,043; HEAD at 313f6df; the four provider modules
verified byte-identical — `git diff HEAD -- providers.py provider_health.py
provider_gate.py provider_manifest.py` = empty):
- provider_call_plan.py             (new, 490 lines)
- provider_response.py              (new, 387 lines)
- tests/test_provider_call_plan.py  (new, 642 lines — 75 tests)
- tests/test_provider_response.py   (new, 478 lines — 79 tests)
- docs/PROVIDERS.md                 (+46 lines — call-plan/normalizer section)
Out of scope (deliberate, do not flag):
- Live execution, credential resolution, server/UI wiring, new gate actions.
- Handoff docs in docs/reviews/ and tools/codex stay untracked/unstaged.
- No commit until VERDICT: APPROVE (explicit gate this WP).

DESIGN (key points for review)
- build_call_plan(config, manifest, gate, run_id=, action=): pure,
  deterministic, stateless. Verifies requested run/action, gate structure/
  version/content-hash, acknowledgment SEMANTICS (statement must name
  provider + action — a hash-self-consistent hollow ack is rejected), and
  the full run/action/provider/config-fingerprint binding of BOTH gate and
  manifest. Planning is read-only over the gate: NO single-use ledger is
  touched, so a planned gate stays fresh for provider_gate's burning seam.
- INTENTIONAL PROPERTY (a): gates are single-use for EXECUTION, but
  planning is unbounded — build_call_plan deliberately takes no ledger.
  That is the design, not a bypass; burning stays in execute_gated_action.
- INTENTIONAL PROPERTY (b): gate_id/manifest_id/plan_id are checksums, not
  MACs — semantic cross-field checks are the actual boundary. Two guards
  exist for forged-but-hash-consistent artifacts: (1) manifest cross-field
  consistency (subscription manifests must be manual_session with service +
  manual_instruction and NO http; API manifests must carry http and no
  manual fields; access must match the registry) — silent field-dropping is
  impossible; (2) the differential corpus test
  (test_planner_and_burning_executor_agree_on_every_gate_mutation) proves
  the non-burning verifier accepts a gate iff execute_gated_action would.
- API plans carry ONLY the manifest's request shape (deep-copied), the
  provider/run/action/gate/manifest identifiers, and an explicit
  future-executor requirement (executor=future_live_adapter,
  operator_gate_required=true, executable_in_this_build=false). No secret
  values, resolved credentials, cookies, browser profiles, or subscription
  tokens; public_view screens every emission.
- Subscription plans (chatgpt/claude): mode manual_operator, executor
  human_operator, manifest manual_instruction preserved; NO http/request/
  transport field exists — automation is unrepresentable (asserted over the
  plan's FULL key set), and execute_call_plan rejects a manual plan that
  carries a request.
- execute_call_plan: fail-closed order transport → resolver → live →
  structure/version/tamper/mode → provider binding → config fingerprint;
  EVERY attempt (valid plans included, all seven modes) terminates in
  LiveProviderExecutionBlockedError (reused from provider_manifest, per the
  work order). Stateless — repeated refusals byte-identical.
- normalize_health_response(config, response): pure; order is validate
  config → secret screen (typed, no echo — checked across args/repr/attrs/
  __cause__/__context__ chain) → bounds (max 512 items / 256-char strings /
  depth 6 / 64 keys / closed JSON scalar set, so no raw TypeError can
  escape) → shape validation. response_sha256 is computed over the
  SCREENED canonical body only. Accepted families: OpenAI-compatible model
  list (openai/moonshot/deepseek), Anthropic model list, Ollama tag list
  (200); provider-matched auth-error bodies on 401/403 for api_key modes
  ONLY (ollama_local is no-auth → 200 only). Statuses like 429/500 are
  DELIBERATELY out of contract (bounded fail-closed, documented) — not an
  oversight. Summary = provider/access/operation/mode/classification/
  status or manual_result/safe count/response hash — never the raw body,
  never model ids, never error messages, never notes.
- Subscription normalization accepts only a manual operator record
  ({mode:"manual", service, operator_result, note?}); any HTTP-shaped input
  (status/body/headers/http/response keys) raises
  AutomatedSubscriptionResponseError.

PRE-REVIEW ALREADY RUN (direct `codex exec` read-only pass, GPT-family)
Three blockers were raised; two were confirmed real and FIXED in this diff,
one is rebutted below. Verify the fixes rather than re-deriving them:
- FIXED: a hash-self-consistent forged manifest could inject foreign fields
  (e.g. `cookie`) into the http block, which planning deep-copied into
  plan["request"]. Fix: manifests are deterministic functions of the
  config, so _verify_manifest_binding now requires the manifest to EQUAL a
  canonical `build_health_request(config)` rebuild — foreign fields cannot
  survive. Regression tests: test_forged_manifest_injecting_fields_into_http
  _is_rejected, test_forged_manifest_altering_the_request_target_is_rejected.
- FIXED: non-JSON adversarial values nested in a gate/plan (e.g. object())
  reached a raw TypeError inside hash recomputation. Fix: _sha256_of now
  converts serialization TypeError/ValueError into the artifact's typed
  validation error. Regression tests: test_non_json_values_in_a_gate_fail_
  typed_not_raw, test_non_json_values_in_a_plan_fail_typed_not_raw.
  (Upstream note, NOT in this diff's scope: provider_gate._gate_id_for has
  the same raw-TypeError exposure for non-JSON adversarial gates — a
  candidate for a later WP since that module may not be modified here. The
  differential corpus test is JSON-domain by construction, so the planner
  being strictly typed where the committed executor would raise raw
  TypeError is a deliberate, documented asymmetry.)
- REBUTTED (your call): the third finding claimed response_sha256 hashes
  the "unscreened" body. Order of operations is validate-config → secret
  screen (reject, no echo) → bounds → shape → hash, so no known-shape
  secret can reach hashing; what the hash commits to is screened,
  health-check-grade content (model lists, generic auth errors). Residual
  risk is a confirmation/dictionary attack on low-entropy NON-secret body
  content — accepted deliberately because the work order asks for a
  response hash for audit linkage, and documented in docs/PROVIDERS.md
  ("sha256 of the screened response"). If you rule the digest should be
  dropped or projected anyway, say so — it is a small, contained change.

TESTS
Command: python3.13 -m pytest tests/test_provider_call_plan.py tests/test_provider_response.py -q
Result: 154 passed in 0.07s
Command: python3.13 -m pytest -q   (full offline suite)
Result: 516 passed in 62.71s — zero failures (prior 362 intact)
Test-first evidence: collection ImportError captured before the modules
existed (red: "2 errors during collection"), 145 green after; +5 hardening
tests added post-advisor (forged manifests, differential gate corpus,
reordered-input byte-identity, exception-chain walker) and +4 post-codex
regression tests (http field injection, target alteration, non-JSON typed
failures), 154 green; two initial test bugs (drifted config identical to
original) found and fixed on the test side — module behavior was already
correct.
Coverage: all seven modes planned + executed-and-blocked; gate/manifest/
config tampering, forgery-with-recomputed-hash, and stale binding;
subscription manual boundary on both the plan side and the response side;
each provider response shape (ok + auth_rejected + malformed/cross-provider
matrices); raw-response exclusion; secret screening incl. the auth-error-
echoes-the-key case; resolver/transport/live injection; canonical hashes;
purity/no-mutation; env-independence with sk- sentinel; no-I/O source scans
on both modules (substring scan per repo convention, extended with open(/
import os/import pathlib).

SCRUTINIZE (threat model, not style)
1. Find any path where a plan or summary carries secret material, a raw
   response, or a live-property claim — including via exception text,
   hashes of unscreened data, or tampered input.
2. Find any way build_call_plan weakens the gate: can a gate the burning
   executor would reject produce a plan? (The differential test says no —
   attack the test.)
3. Find any hand-crafted, hash-self-consistent artifact (gate, manifest,
   plan) that bypasses a semantic check or reaches a raw KeyError/TypeError
   instead of a typed ProviderConfigError.
4. Honest uncertainty to weigh: (a) gate/manifest verification is
   re-implemented here from the public identity contract rather than
   importing provider_gate's private helpers — duplication was chosen over
   private-API coupling, and the differential test pins the equivalence;
   say if you'd rather see the helpers exported upstream in a later WP;
   (b) the secret deny-list breadth (sk-/eyJ/ghp_/AKIA/Bearer …) is
   providers.py's committed contract — extending it (hf_, AIza, xai-, URL
   userinfo) would be a registry WP, not this one; (c) response bounds
   (512/256/6/64) are asserted constants — say if they should be exported
   named constants for the future adapter to share.

HARD STOP
No network or provider call anywhere; no credential resolution; the four
provider modules and the engine checkout untouched; nothing committed —
commit is gated on VERDICT: APPROVE.
