HANDOFF: WebUI autonomous PAI controller

INTENT
Provide the separate WebUI repository with a bounded Claude/PAI loop that
implements one UI stage at a time, runs tests, sends the exact staged tree to
Codex, and commits only an approved tree.

SCOPE
`tools/pai_loop.py` only. The controller uses a one-hour Claude timeout,
fail-closed tree/parent checks, review-result handling, dispute escalation,
and the offline stop boundary defined by `docs/UI_BUILD_LOOP.md`.

TESTS
`python -m py_compile tools/pai_loop.py` — passed
`python -m pytest -q` — 11 passed
`git diff --check` — passed

HARD STOP
The controller does not enable live APIs, load credentials, modify either
engine repository, or commit without independent Codex approval.
