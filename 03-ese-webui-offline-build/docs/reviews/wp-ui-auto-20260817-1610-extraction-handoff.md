HANDOFF: WebUI stage 7 — source-anchored extraction

INTENT
Add one bounded offline WebUI action that drives the pinned engine
`screen_prove.cli extract` contract after completed screening.

SCOPE
`bridge.py`, `server.py`, `store.py`, `static/index.html`, `static/app.js`,
`README.md`, `docs/UI_BUILD_LOOP.md`, the stage-7 compatibility receipt, and
focused WebUI tests. The action accepts an explicitly supplied UTF-8 source
and field specification, preserves the source under `extraction/source/`,
and verifies the engine-produced extraction record before marking the stage
complete. It does not retrieve full text or call any live connector.

GATES AND FAILURE BEHAVIOR
- `extract` requires `screen` to be complete.
- Reported fields must match exact source spans; `not_reported` is NR; and
  `estimated` requires a non-empty calculation note.
- The WebUI verifies the source hash, provenance path, requested field names,
  requested statuses, field count, non-empty reported spans, field invariants,
  and record hash before recording completion.
- Invalid UTF-8, engine failure, missing output, or malformed output leaves
  the extraction stage pending and never leaves a completed record artifact.
- Every retry invalidates the prior extraction artifact and stage first, so a
  malformed retry cannot preserve stale success.
- Re-running import, deduplication, locking, or screening removes any
  extraction artifact and resets its stage to pending, because extraction is
  bound to the upstream screened population.

TESTS
`python -m pytest -q tests/test_webui.py -k stage_seven_extraction_runs_against_real_engine_cli`
— 1 passed, 111 deselected
`python -m pytest -q tests/test_webui.py -k 'extract or upstream_rerun'` — 18 passed, 97 deselected
`python -m pytest -q` — 136 passed in 51.92s
`python -m py_compile server.py bridge.py store.py` — passed
`node --check static/app.js` — passed

COMPATIBILITY
Engine commit: `30c393458c53c9944a452a888097b69753d01133`
Engine contract: `extraction-record-v1`
Compatibility note: `docs/reviews/UI_EXTRACTION_COMPATIBILITY_RECEIPT.md`

HARD STOP
No live APIs, network calls, credentials, subscriptions, institutional
access, or external databases were used. Independent Codex review is required
before commit. Risk-of-bias, synthesis, full-stack qualification, and live
qualification remain separate stages.
