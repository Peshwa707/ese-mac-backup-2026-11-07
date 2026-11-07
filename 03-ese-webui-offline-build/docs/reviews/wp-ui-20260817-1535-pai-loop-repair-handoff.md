HANDOFF: WebUI autonomous PAI controller — gate repair

INTENT
Repair the controller’s approval authentication, continuous review handling,
offline compatibility stop, and controller-specific test coverage.

SCOPE
`tools/pai_loop.py` and `tests/test_pai_loop.py`.

TESTS
`python -m py_compile tools/pai_loop.py` — passed
`python -m pytest -q` — 24 passed
`git diff --check` — passed

HARD STOP
The loop authenticates Codex review messages, preserves continuous re-review
without an arbitrary round cap, escalates explicit disputes, and writes an
authoritative stop receipt after the compatibility stage. No live APIs,
credentials, or engine repository changes were used.
