HANDOFF: WebUI offline queue work package — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: docs/UI_BUILD_LOOP.md, server.py, tests/test_webui.py

TESTS
`pytest -q` — 115 passed in 42.23s

DIFF STAT
```text
README.md                                          |  60 +-
 bridge.py                                          |  49 +
 docs/UI_BUILD_LOOP.md                              | 130 ++-
 docs/reviews/wp-ui-auto-20260817-134524-handoff.md |  28 +
 docs/reviews/wp-ui-auto-20260817-135243-handoff.md |  29 +
 server.py                                          | 443 ++++++++-
 static/app.js                                      |  59 +-
 static/index.html                                  |  35 +-
 store.py                                           |   6 +-
 tests/test_webui.py                                | 988 ++++++++++++++++++++-
 10 files changed, 1710 insertions(+), 117 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
