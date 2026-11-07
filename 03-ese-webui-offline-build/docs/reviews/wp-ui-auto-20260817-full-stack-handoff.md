HANDOFF: WebUI final offline qualification — stages 1 through 9

INTENT
Qualify the isolated WebUI and pinned offline engine together through the
current complete local workflow and write the final offline receipt.

SCOPE
`tests/test_webui.py` adds the final end-to-end fixture and an explicit
synthesis missing-output regression. `docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md`
records the evidence and claim ceiling. No production connector, model,
network, credential, or subscription path is added.

ACCEPTANCE EVIDENCE
- One local fixture crosses doctor, import, dedup, lock, screen, extraction,
  RoB, synthesis, package, and replay.
- Cross-phase record identity remains stable and the extraction source hash
  matches exact local source bytes.
- All stage artifacts exist and replay reports byte stability.
- Synthesis false-success with no output fails closed.
- Real local CLI compatibility tests for extraction, RoB, and synthesis pass.
- Full WebUI suite passes.

TESTS
`python -m pytest -q tests/test_webui.py -k 'synthesis or full_offline_stack'` — 8 passed, 124 deselected
`python -m pytest -q` — 153 passed in 62.58s
`python -m py_compile server.py bridge.py store.py` — passed
`node --check static/app.js` — passed

HARD STOP
The receipt is an offline engineering gate only. No live API qualification,
database access, credentials, institutional access, purchases, or external
communication is authorized by this handoff. Independent review is required
before the final receipt is committed.
