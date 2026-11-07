HANDOFF: WebUI operator shell stage — diagnostic surface repair

INTENT
Ensure stage-1 doctor diagnostics are current, fail closed on stale reports,
and visible to the operator.

SCOPE
`bridge.py` diagnostic freshness, the stage-1 static diagnostic surface, and
behavioral/static-asset regression tests. Later pipeline stages remain
deferred.

TESTS
`python -m pytest -q` — 32 passed
`python -m py_compile tools/pai_loop.py` — passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Independent Codex review is required.
