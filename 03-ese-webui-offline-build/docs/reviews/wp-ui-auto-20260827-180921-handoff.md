HANDOFF: WebUI offline queue work package — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: tests/test_webui.py

TESTS
`pytest -q` — 627 passed in 81.39s (0:01:21)

DIFF STAT
```text
docs/UI_BUILD_LOOP.md                              |  48 ++++
 docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md         |  74 +++---
 docs/reviews/wp-ui-auto-20260827-180528-handoff.md |  23 ++
 tests/test_webui.py                                | 250 +++++++++++++++++++++
 4 files changed, 355 insertions(+), 40 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
