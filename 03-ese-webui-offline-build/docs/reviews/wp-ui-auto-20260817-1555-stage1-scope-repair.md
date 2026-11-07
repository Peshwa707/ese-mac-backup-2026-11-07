HANDOFF: WebUI operator shell stage — scope repair

INTENT
Keep the operator-shell package strictly within UI stage 1 after review found
stage-3 hashing and stage-5 artifact inspection bundled into it.

SCOPE
Basic run creation/status/artifact listing and stage-1 UI behavior only.
Later-stage hashing, byte inspection, and replay reporting are deferred to
their own independently reviewed work packages.

TESTS
`python -m pytest -q` — 28 passed
`python -m py_compile tools/pai_loop.py` — passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Independent Codex review is required.
