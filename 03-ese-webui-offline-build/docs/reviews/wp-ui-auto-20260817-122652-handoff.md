HANDOFF: WebUI offline queue work package — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: server.py, static/app.js, tests/test_webui.py

TESTS
`pytest -q` — 44 passed in 9.66s

DIFF STAT
```text
README.md                                          |  31 +-
 bridge.py                                          |  22 ++
 docs/UI_BUILD_LOOP.md                              |  22 +-
 docs/reviews/wp-ui-auto-20260817-122334-handoff.md |  27 ++
 server.py                                          |  98 +++++-
 static/app.js                                      |  41 ++-
 static/index.html                                  |  24 +-
 tests/test_webui.py                                | 339 ++++++++++++++++++++-
 8 files changed, 552 insertions(+), 52 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
