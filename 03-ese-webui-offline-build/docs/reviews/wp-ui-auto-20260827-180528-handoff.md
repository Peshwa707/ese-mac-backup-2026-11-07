HANDOFF: WebUI offline queue work package

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: docs/UI_BUILD_LOOP.md, docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md, tests/test_webui.py

TESTS
`pytest -q` — 627 passed in 84.46s (0:01:24)

DIFF STAT
```text
docs/UI_BUILD_LOOP.md                      |  48 +++++++
 docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md |  74 +++++------
 tests/test_webui.py                        | 204 +++++++++++++++++++++++++++++
 3 files changed, 286 insertions(+), 40 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
