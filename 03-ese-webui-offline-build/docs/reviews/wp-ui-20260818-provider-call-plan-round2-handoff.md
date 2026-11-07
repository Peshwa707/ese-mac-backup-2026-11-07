HANDOFF: wp-ui-provider-call-plan — round 2 (addresses your VERDICT: CHANGES
on rq-47fac5b7f4be)

YOUR BLOCKER, RESTATED
execute_call_plan validated only top-level keys, hash, and mode/request
presence — a hash-self-consistent forged plan (arbitrary request host or
cookie fields, execution flags flipped to operator_gate_required=false /
executable_in_this_build=true, forged gate_id/manifest_id) reached the
terminal blocked exception as if "verified", instead of being rejected.

THE FIX (staged; provider_call_plan.py)
The seam now requires the gate itself and closes the forgery space the same
way round 1 closed it for manifests — canonical-rebuild equality:

- New signature: `execute_call_plan(plan, config, gate, *, resolver=None,
  transport=None, live=False)`. A plan is gate-BOUND; presenting it without
  the gate is not an execution attempt. A missing/bogus gate is a typed
  GateValidationError.
- Fail-closed order: transport → resolver → live (all
  LiveProviderExecutionBlockedError) → plan structure/version/tamper/mode →
  plan-vs-config fingerprint (CallPlanStaleError on drift) → provider
  binding → full non-burning verify_gate_binding(gate, config,
  run_id=plan.run_id, action=plan.action) → **plan must EQUAL
  build_call_plan(config, build_health_request(config), gate, ...)**.
  Plans are deterministic functions of (config, manifest, gate), and
  manifests of config — so every forged field, identifier, or flag fails
  the equality, typed, BEFORE the terminal block.
- Still stateless and non-burning: this seam never executes, so it never
  consumes the gate; burn-on-attempt remains provider_gate's contract at
  the real (future) execution edge. Repeated refusals are byte-identical.
- docs/PROVIDERS.md seam bullet updated to match.

NEW REGRESSION TESTS (tests/test_provider_call_plan.py)
- test_forged_plan_request_fields_are_rejected_at_the_seam (cookie in
  request, recomputed plan_id)
- test_forged_plan_request_target_is_rejected_at_the_seam (host swap)
- test_forged_execution_flags_are_rejected_at_the_seam (your exact flip)
- test_forged_plan_identifiers_are_rejected_at_the_seam (gate_id,
  manifest_id)
- test_forged_plan_run_id_is_rejected_by_gate_binding
- test_plan_for_one_gate_cannot_execute_with_another (real second gate,
  different ack → different gate_id → rejected)
- test_execution_without_the_bound_gate_is_impossible (None/{}/str/int)
All prior tests updated for the new signature; every execute path passes
the real gate.

TESTS
Command: python3.13 -m pytest tests/test_provider_call_plan.py tests/test_provider_response.py -q
Result: 164 passed in 0.08s (85 call-plan + 79 response)
Command: python3.13 -m pytest -q   (full offline suite)
Result: 526 passed in 62.51s — zero failures

SCOPE UNCHANGED
Same 5 staged files (now +2,144 lines): provider_call_plan.py (516),
provider_response.py (387, untouched this round),
tests/test_provider_call_plan.py (717), tests/test_provider_response.py
(478, untouched this round), docs/PROVIDERS.md (+53). Four provider
modules still byte-identical to HEAD 313f6df; nothing committed; commit
remains gated on your VERDICT: APPROVE.
