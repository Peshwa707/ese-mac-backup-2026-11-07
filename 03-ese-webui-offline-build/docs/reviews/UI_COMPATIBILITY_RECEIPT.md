# UI Compatibility Receipt

STATUS: pending -- not yet verified.

This file previously asserted `UI_COMPATIBILITY_VERIFIED: true`, citing a
"real-engine full pipeline test" and a `python -m pytest -q` run of 117
tests. That claim has been withdrawn: no such test
(`test_stage_six_full_pipeline_runs_against_real_engine_cli`, or any other
skip-guarded test invoking the real, un-stubbed `EngineBridge` against the
isolated engine checkout) existed anywhere in `tests/test_webui.py` at the
time that claim was written, so the "117 passed" figure and the cited
review verdict could not have covered the scenario this receipt described.
Per `docs/UI_BUILD_LOOP.md` ("Compatibility" stage 6), this receipt may
only assert `UI_COMPATIBILITY_VERIFIED: true` after the pinned
engine-artifact contract check genuinely, currently passes against the
isolated engine checkout *and* an independent reviewer confirms it -- not
before.

## Current state

- `tests/test_webui.py::test_stage_six_full_pipeline_runs_against_real_engine_cli`
  has now been added (test-first): it drives run creation, `import`,
  `dedup`, `lock`, a `human_adjudication` "on" toggle, `screen` (resolving
  via `adjudicate` if the real engine's cached screening result abstains),
  `package`, and `replay` through the real, un-stubbed `EngineBridge`
  against the isolated offline engine checkout.
- Like every other real-engine test in this file, it is skipped (not
  failed) when the isolated engine checkout is not present locally, uses
  no stub, no network access, and no credentials, and does not affect this
  package's own offline stub test suite.
- Whether this test, and the rest of this package's offline suite,
  actually, currently passes against the isolated engine checkout has not
  been asserted here -- that confirmation, and the independent review it
  requires, is a separate, later step this file does not perform on its
  own.

`UI_COMPATIBILITY_VERIFIED: true` must not be written to this file until
that confirmation and independent review have genuinely happened.

- Repository: `evidence-synthesis-engine-webui`
- Engine checkout: `/Users/neurologic/Projects/evidence-synthesis-engine-screen-prove`
- Scope: local offline CLI compatibility only

This receipt does not authorize live APIs, credentials, subscriptions,
institutional access, scientific validation, or production use.
