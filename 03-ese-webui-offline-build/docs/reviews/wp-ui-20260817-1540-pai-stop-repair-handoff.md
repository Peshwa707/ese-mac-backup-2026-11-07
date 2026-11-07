HANDOFF: WebUI autonomous PAI controller — authoritative stop repair

INTENT
Remove reliance on Claude-reported stage text for the irreversible offline
stop.

SCOPE
`tools/pai_loop.py` and `tests/test_pai_loop.py`. The controller now checks
the exact compatibility receipt path and marker from the exact reviewer-
approved Git tree before writing its stop receipt.

TESTS
`python -m py_compile tools/pai_loop.py` — passed
`python -m pytest -q` — 28 passed
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Continuous re-review and explicit dispute
escalation remain enabled.
