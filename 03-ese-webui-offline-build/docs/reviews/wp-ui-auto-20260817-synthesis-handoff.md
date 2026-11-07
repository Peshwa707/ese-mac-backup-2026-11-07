HANDOFF: WebUI stage 9 — bounded single-study synthesis

INTENT
Add one bounded offline WebUI action that drives the pinned engine
`synthesize` contract for the same extracted and RoB-assessed record.

SCOPE
`bridge.py`, `server.py`, `store.py`, `static/index.html`, `static/app.js`,
`README.md`, `docs/UI_BUILD_LOOP.md`, the stage-9 compatibility receipt, and
focused WebUI tests. The action preserves the submitted count specification
under `synthesis/spec.json`, invokes the local engine, and verifies one
single-study calculation. It does not pool studies or write treatment
conclusions.

GATES AND FAILURE BEHAVIOR
- `synthesize` requires completed `rob` and extraction stages.
- The requested record ID must match the extracted included record and the
  risk-of-bias artifact.
- Measure, four counts, calculated or `insufficient_data` status, point
  estimate, confidence interval, uncertainty flags, and `calculation_hash`
  are verified by a local golden recomputation.
- Engine failure, missing output, malformed output, invalid counts, or a
  mismatched calculation leaves the stage pending and removes `synthesis.json`;
  the submitted specification remains for diagnosis.
- Re-running RoB, extraction, or any upstream stage removes the synthesis
  artifact and resets the stage to pending.

TESTS
`python -m pytest -q tests/test_webui.py -k synthesis` — 6 passed, 124 deselected
`python -m pytest -q tests/test_webui.py -k stage_nine_synthesis_runs_against_real_engine_cli` — 1 passed, 129 deselected
`python -m pytest -q` — 151 passed in 61.55s
`python -m py_compile server.py bridge.py store.py` — passed
`node --check static/app.js` — passed

COMPATIBILITY
Engine commit: `30c393458c53c9944a452a888097b69753d01133`
Engine contract: `synthesis-calculation-v1`
Compatibility note: `docs/reviews/UI_SYNTHESIS_COMPATIBILITY_RECEIPT.md`

HARD STOP
No live APIs, network calls, credentials, subscriptions, institutional
access, or external databases were used. Independent Codex review is required
before commit. Full-stack qualification and live qualification remain separate
stages.
