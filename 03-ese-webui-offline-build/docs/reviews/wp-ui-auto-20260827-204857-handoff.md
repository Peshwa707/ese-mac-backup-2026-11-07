HANDOFF: WebUI offline queue work package — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: docs/reviews/wp-ui-auto-20260827-203845-handoff.md, docs/reviews/wp-ui-auto-20260827-204209-handoff.md, tests/test_webui.py

TESTS
`pytest -q` — 630 passed in 82.23s (0:01:22)

DIFF STAT
```text
docs/reviews/wp-ui-auto-20260827-203845-handoff.md |  14 +++
 docs/reviews/wp-ui-auto-20260827-204209-handoff.md |  68 +++++++++++++
 tests/test_webui.py                                | 106 +++++++++++++++++++++
 3 files changed, 188 insertions(+)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
