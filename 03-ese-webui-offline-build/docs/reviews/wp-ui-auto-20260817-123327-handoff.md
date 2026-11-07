HANDOFF: WebUI offline queue work package — re-review — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: server.py, tests/test_webui.py

TESTS
`pytest -q` — 46 passed in 11.28s

DIFF STAT
```text
README.md                                          |  31 +-
 bridge.py                                          |  44 ++
 docs/UI_BUILD_LOOP.md                              |  22 +-
 docs/reviews/wp-ui-auto-20260817-122334-handoff.md |  27 ++
 docs/reviews/wp-ui-auto-20260817-122652-handoff.md |  28 ++
 docs/reviews/wp-ui-auto-20260817-123057-handoff.md |  29 ++
 server.py                                          | 141 +++++-
 static/app.js                                      |  41 +-
 static/index.html                                  |  24 +-
 tests/test_webui.py                                | 478 ++++++++++++++++++++-
 10 files changed, 813 insertions(+), 52 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
