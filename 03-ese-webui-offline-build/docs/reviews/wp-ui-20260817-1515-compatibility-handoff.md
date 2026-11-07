HANDOFF: WebUI foundation — real engine compatibility repair

INTENT
Resolve the real offline integration failure found after the boundary repair.

SCOPE
Use a recognized RIS filename for the default import path and add a regression
test for the WebUI-to-engine format contract.

TESTS
`python -m pytest -q` — 11 passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
