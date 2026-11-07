HANDOFF: WebUI operator shell stage

INTENT
Complete UI stage 1: a local operator surface that creates/selects runs,
reports offline status, and lists artifacts without agent operation.

SCOPE
Operator-shell UI affordances and run-store behavior in `server.py`,
`store.py`, `static/`, and `tests/test_webui.py`, plus the LaunchAgent-safe
pytest invocation in `tools/pai_loop.py`.

TESTS
`python -m py_compile tools/pai_loop.py` — passed
`python -m pytest -q` — 32 passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Independent Codex review is required before
commit.
