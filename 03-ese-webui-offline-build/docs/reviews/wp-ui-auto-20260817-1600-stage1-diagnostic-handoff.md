HANDOFF: WebUI operator shell stage — diagnostic boundary repair

INTENT
Keep UI stage 1 genuinely operator-only and preserve structured doctor
diagnostics when readiness checks report warnings.

SCOPE
Stage-1-only server/UI surface, basic artifact listing, stage-1 documentation,
and diagnostic subprocess handling. Import, deduplication, lock, screening,
package, replay, hashing, and byte inspection remain deferred.

TESTS
`python -m pytest -q` — 29 passed
`python -m py_compile tools/pai_loop.py` — passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Independent Codex review is required.
