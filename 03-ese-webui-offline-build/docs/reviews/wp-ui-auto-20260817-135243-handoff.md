HANDOFF: WebUI offline queue work package — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: docs/UI_BUILD_LOOP.md, tests/test_webui.py

TESTS
`pytest -q` — 113 passed in 41.06s

DIFF STAT
```text
README.md                                          |  60 +-
 bridge.py                                          |  49 ++
 docs/UI_BUILD_LOOP.md                              | 118 ++-
 docs/reviews/wp-ui-auto-20260817-134524-handoff.md |  28 +
 server.py                                          | 423 +++++++++-
 static/app.js                                      |  59 +-
 static/index.html                                  |  35 +-
 store.py                                           |   6 +-
 tests/test_webui.py                                | 862 ++++++++++++++++++++-
 9 files changed, 1524 insertions(+), 116 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
