HANDOFF: WebUI offline queue work package — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: docs/UI_BUILD_LOOP.md, server.py, tests/test_webui.py

TESTS
`pytest -q` — 88 passed in 30.44s

DIFF STAT
```text
README.md                                          |  58 +-
 bridge.py                                          |  26 +
 docs/UI_BUILD_LOOP.md                              | 104 ++-
 docs/reviews/wp-ui-auto-20260817-131116-handoff.md |  28 +
 docs/reviews/wp-ui-auto-20260817-131538-handoff.md |  29 +
 server.py                                          | 391 +++++++++-
 static/app.js                                      |  86 +++
 static/index.html                                  |  31 +-
 static/styles.css                                  |   3 +
 tests/test_webui.py                                | 825 ++++++++++++++++++++-
 10 files changed, 1469 insertions(+), 112 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
