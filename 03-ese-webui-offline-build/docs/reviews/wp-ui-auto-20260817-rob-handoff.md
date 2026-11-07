HANDOFF: WebUI stage 8 — bounded risk-of-bias assessment

INTENT
Add one bounded offline WebUI action that drives the pinned engine `rob`
contract for the same positively included record whose extraction is complete.

SCOPE
`bridge.py`, `server.py`, `store.py`, `static/index.html`, `static/app.js`,
`README.md`, `docs/UI_BUILD_LOOP.md`, the stage-8 compatibility receipt, and
focused WebUI tests. The action preserves the submitted specification under
`rob/spec.json`, invokes the local engine, and verifies the returned
`rob-assessment-v1` artifact before marking the stage complete. It does not
retrieve evidence, call a model, or perform synthesis.

GATES AND FAILURE BEHAVIOR
- `rob` requires `extraction` to be complete.
- The requested `record_id` must match the extracted record, which is already
  bound to a final include decision; unknown and excluded records are refused.
- Domain names, judgments, count, tool, overall judgment, rationale,
  `source_links`, explicit `not_assessed`, and `rob_hash` are verified.
- Engine failure, missing output, or malformed output leaves the stage
  pending and removes `rob.json`; the submitted spec remains for diagnosis.
- Re-running extraction or any upstream stage removes `rob.json` and
  `rob/spec.json` and resets `rob` to pending.

TESTS
`python -m pytest -q tests/test_webui.py -k 'rob or risk_of_bias'` — 9 passed, 115 deselected
`python -m pytest -q tests/test_webui.py -k stage_eight_rob_runs_against_real_engine_cli` — 1 passed, 121 deselected
`python -m pytest -q` — 145 passed in 57.42s
`python -m py_compile server.py bridge.py store.py` — passed
`node --check static/app.js` — passed

COMPATIBILITY
Engine commit: `30c393458c53c9944a452a888097b69753d01133`
Engine contract: `rob-assessment-v1`
Compatibility note: `docs/reviews/UI_ROB_COMPATIBILITY_RECEIPT.md`

HARD STOP
No live APIs, network calls, credentials, subscriptions, institutional
access, or external databases were used. Independent Codex review is required
before commit. Synthesis, full-stack qualification, and live qualification
remain separate stages.
