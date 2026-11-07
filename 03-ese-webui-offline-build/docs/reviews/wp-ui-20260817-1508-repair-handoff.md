HANDOFF: WebUI offline operator foundation — boundary repair

INTENT
Address the independent review findings for the initial offline operator
surface before its first commit.

SCOPE
Stage prerequisites, credential-isolated engine subprocesses, health claims,
and behavioral tests for action ordering, adjudication, artifacts, and
environment isolation.

TESTS
`python -m pytest -q` — 7 passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the isolated engine repositories were used. Independent Codex review is
required before commit.
