HANDOFF: WebUI operator shell stage — diagnostic schema hardening

INTENT
Fail closed when the engine doctor report omits or mis-types its required
boolean status.

SCOPE
Strict `passed` field validation in `bridge.py` and regression tests for
missing and non-boolean diagnostic status. Stage-1-only scope is preserved.

TESTS
`python -m pytest -q` — 34 passed
`python -m py_compile tools/pai_loop.py` — passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Independent Codex review is required.
