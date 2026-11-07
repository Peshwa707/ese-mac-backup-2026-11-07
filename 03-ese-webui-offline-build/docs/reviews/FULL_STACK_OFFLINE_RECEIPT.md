# ESE WebUI full offline qualification receipt

STATUS: pending -- not yet verified.

This file previously asserted `FULL_STACK_OFFLINE_VERIFIED: true`, but no
corresponding test (`test_full_offline_stack_runs_stages_one_through_nine_and_replays`)
existed anywhere in `tests/test_webui.py` at the time that claim was written,
and no independent review record for it could be found under
`docs/reviews/`. Per `docs/UI_BUILD_LOOP.md` ("Full offline stack" phase 10),
this receipt may only assert `FULL_STACK_OFFLINE_VERIFIED: true` after the
pinned engine-artifact contract genuinely passes *and* an independent
reviewer confirms it -- not before. That prior claim has been withdrawn as
inaccurate rather than left in place.

## Current state

- `tests/test_webui.py::test_full_offline_stack_runs_stages_one_through_nine_and_replays`
  has now been added (test-first): it drives run creation, `doctor`,
  `import`, `dedup`, `lock`, `screen` (resolving via `adjudicate` if the real
  engine's cached screening result abstains), `extract`, `rob`,
  `synthesize`, `package`, and `replay` through the real, un-stubbed
  `EngineBridge` against the isolated offline engine checkout, in one
  continuous run, and checks cross-phase record-id provenance plus a
  byte-stable `package`/`replay` taken after extraction/RoB/synthesis have
  also written their own artifacts.
- Like every other real-engine test in this file, it is skipped (not
  failed) when the isolated engine checkout is not present locally, uses no
  stub, no network access, and no credentials, and does not affect this
  package's own offline stub test suite.
- Since that entry above was first written, `tests/test_webui.py` has also
  grown the always-run (never skip-guarded) offline coverage of the same
  phase-10 scope that this document's "Phase 10 status" section narrates in
  full: `test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays`
  and
  `test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays_with_post_package_byte_stability_check`
  (one continuous run through every current stage, entirely via the
  deterministic stub bridge), `test_stub_full_offline_stack_with_human_adjudication_toggle_runs_stages_one_through_nine_and_replays`
  (the same, but forcing the `human_adjudication` toggle/`adjudicate`
  "blocked" branch unconditionally rather than only when a real cached
  screening result happens to abstain),
  `test_human_adjudication_toggle_on_invalidates_extraction_rob_and_synthesis_artifacts_and_stages`
  (proving the toggle's retroactive "complete" -> "blocked" screen demotion
  invalidates extraction/RoB/synthesis, not merely package/replay),
  `test_bridge_extract_record_invokes_expected_cli_shape` /
  `test_bridge_extract_record_raises_bridge_error_on_nonzero_exit`,
  `test_bridge_rob_assessment_invokes_expected_cli_shape` /
  `test_bridge_rob_assessment_raises_bridge_error_on_nonzero_exit`, and
  `test_bridge_synthesize_invokes_expected_cli_shape` /
  `test_bridge_synthesize_raises_bridge_error_on_nonzero_exit` (direct
  `EngineBridge`-layer CLI-shape coverage for the three stage 7-9 bridge
  methods phase 10 exercises), and
  `test_no_duplicate_top_level_test_function_names_in_this_file` (a
  mechanical, `ast`-based guard against the exact silent-shadowing failure
  mode this file has already suffered from once). A verification pass of
  `server.py`, `bridge.py`, and `tests/test_webui.py` performed while
  preparing this update found every one of these already present and
  internally consistent with `docs/UI_BUILD_LOOP.md`'s Phase 10 status
  section; no additional server-side or bridge-side code path was added or
  changed as part of that verification.
- Whether this test, and the rest of this package's offline suite,
  actually, currently passes against the isolated engine checkout (and
  offline, in the case of the stub-driven tests) has not been asserted
  here -- that confirmation, and the independent review it requires, is a
  separate, later step this file does not perform on its own.

`FULL_STACK_OFFLINE_VERIFIED: true` must not be written to this file until
that confirmation and independent review have genuinely happened.
