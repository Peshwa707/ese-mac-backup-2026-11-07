HANDOFF: wp-ui-provider-gate — per-provider/per-run operator live-test gate + blocked execution seam (round 1)

INTENT
Add the operator gate artifact/API on top of the committed registry
(a7989a8) and health boundary (1ff79e9): a deterministic, single-use,
per-provider/per-run authorization bound to the validated configuration
hash, plus an execution seam that verifies everything and still raises a
typed live-execution-blocked error — so this package can prove an
authorization but structurally cannot make a live call.

SCOPE
Files staged (3 files, +656/−12; providers.py AND provider_health.py
byte-identical to 1ff79e9 — verified `git diff 1ff79e9 -- providers.py
provider_health.py` = empty):
- provider_gate.py            (new, 268 lines)
- tests/test_provider_gate.py (new, 359 lines — 57 deterministic offline tests)
- docs/PROVIDERS.md           (gate section rewritten: artifact exists,
                               execution still not implemented, registry
                               live_enabled rejection unchanged)
Out of scope (deliberate, do not flag):
- Actual live execution, credential resolution, server/UI wiring, gate
  persistence to disk, time-based expiry (clock-free by determinism
  doctrine — staleness is structural).
- Handoff docs in docs/reviews/ and tools/codex stay untracked/unstaged.
- No commit until Osama returns VERDICT: APPROVE.

DESIGN (key points for review)
- issue_gate(config, run_id=…, action=…, operator_ack=…) — all four
  required. run_id must match store.RUN_ID (imported: canonical run-id
  definition, no duplication). SUPPORTED_ACTIONS == ("health_check",).
  operator_ack must be exactly {confirmed: literal True, statement:
  non-empty str naming BOTH the provider id and the action} — blanket acks
  impossible; per-provider/per-run is enforced by structure (one provider,
  one run per gate; no wildcard expressible).
- Determinism/binding: config_sha256 = sha256(to_json(validated config))
  (canonical redacted JSON — references only, secrets structurally
  excluded upstream); gate_id = sha256(to_json(body-without-gate_id)).
  Equal inputs → byte-identical artifacts; tamper detected by
  recomputation. No clocks, no randomness anywhere (source-screened).
- execute_gated_action(gate, config, run_id=…, action=…, used_gates=…,
  transport=None) — verification order: injected transport →
  TransportInjectionError (reused from provider_health); ledger must be a
  caller-owned set (required arg — forgetting it is a TypeError, not
  silent reuse); structure/unknown-fields → GateValidationError; unknown
  gate_version → GateStaleError; gate_id recompute mismatch →
  GateTamperError; ack/run/action re-validation; run or action or provider
  mismatch → GateValidationError; config drift → GateStaleError; replay →
  GateReusedError; then burn-on-attempt and raise
  LiveExecutionNotImplementedError EVEN for a fully valid gate. All errors
  are ProviderConfigError subclasses.
- Interactive chatgpt/claude gates carry execution_note: manual,
  operator-driven, never automated.

TESTS
Command: python3.13 -m pytest tests/test_provider_gate.py -q
Result: 57 passed in 0.02s
Command: python3.13 -m pytest -q   (full offline suite)
Result: 315 passed in 62.46s — zero failures (prior 258 intact)
Test-first evidence: collection ImportError captured before the module
existed (red). One RED test survived implementation and exposed a bug in my
TEST HELPER (ack=None silently fell back to a valid ack via a `None`
default); fixed with a sentinel default so the None case genuinely reaches
issue_gate — worth your eyes on that helper.
Coverage: issuance across all seven modes; fingerprint = sha256 of
canonical validated JSON (independently recomputed in the test); gate_id
content-derivation (recomputed in test); determinism across config key
order; env-independence with sentinel set vs deleted (never serialized);
13 malformed-ack cases; malformed run ids; unsupported actions incl. case
variation; secret-shaped ack rejection; run/provider/config mismatch;
config-drift staleness; version staleness; 5 tamper cases; replay after
burn; execution-without-gate (5 bogus shapes); transport injection (4
shapes); typed-error hierarchy; purity/no-mutation; canonical round-trip;
no-I/O source screen (also bans time/random/date* imports); no
resolver-shaped exports.

SCRUTINIZE (threat model, not style)
1. Find any input that reaches the seam's terminal raise without every
   binding check passing — i.e., any way to "use" a gate for a different
   provider, run, action, or config than it names.
2. Find any way to forge or replay: a hand-built gate dict that passes
   gate_id recomputation without issue_gate, or a reuse that evades the
   ledger (e.g. by passing a fresh set each call — the ledger is
   caller-owned by design; confirm the docs/tests make the caller contract
   unmistakable, since a careless caller with a new set per call loses
   replay protection).
3. Honest uncertainty: (a) clock-free staleness means an untouched
   config's gate never expires by time — deliberate (no clocks =
   determinism), flag if you disagree for a gate artifact; (b) burn-on-
   attempt happens BEFORE the NotImplemented raise — a valid gate is
   consumed by proving validity; confirm that's the right ergonomic;
   (c) ack statement matching is substring-based ("openai_api" and
   "health_check" must appear) — a statement naming two providers would
   pass for either; structure still binds ONE provider, but say if the
   statement check should be stricter.
4. Confirm the docs section can't be read as "live access is now possible."

HARD STOP
No network or provider call anywhere; no credential resolution; engine
checkout, providers.py, provider_health.py, unrelated untracked files all
untouched; nothing committed — commit gated on Osama's VERDICT: APPROVE.
