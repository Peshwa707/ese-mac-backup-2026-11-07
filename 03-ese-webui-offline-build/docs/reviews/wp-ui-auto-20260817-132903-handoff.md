HANDOFF: WebUI offline queue work package — re-review — re-review — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: server.py

TESTS
`pytest -q` — 90 passed in 31.29s

DIFF STAT
```text
README.md                                          |  58 +-
 bridge.py                                          |  26 +
 docs/UI_BUILD_LOOP.md                              | 111 ++-
 docs/reviews/wp-ui-auto-20260817-131116-handoff.md |  28 +
 docs/reviews/wp-ui-auto-20260817-131538-handoff.md |  29 +
 docs/reviews/wp-ui-auto-20260817-132052-handoff.md |  30 +
 docs/reviews/wp-ui-auto-20260817-132646-handoff.md |  31 +
 server.py                                          | 482 ++++++++++-
 static/app.js                                      |  86 ++
 static/index.html                                  |  31 +-
 static/styles.css                                  |   3 +
 tests/test_webui.py                                | 915 ++++++++++++++++++++-
 12 files changed, 1717 insertions(+), 113 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
