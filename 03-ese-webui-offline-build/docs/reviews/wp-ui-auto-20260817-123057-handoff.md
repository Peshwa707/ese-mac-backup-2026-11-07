HANDOFF: WebUI offline queue work package — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: bridge.py, server.py, tests/test_webui.py

TESTS
`pytest -q` — 45 passed in 10.22s

DIFF STAT
```text
README.md                                          |  31 +-
 bridge.py                                          |  44 +++
 docs/UI_BUILD_LOOP.md                              |  22 +-
 docs/reviews/wp-ui-auto-20260817-122334-handoff.md |  27 ++
 docs/reviews/wp-ui-auto-20260817-122652-handoff.md |  28 ++
 server.py                                          | 121 +++++-
 static/app.js                                      |  41 +-
 static/index.html                                  |  24 +-
 tests/test_webui.py                                | 412 ++++++++++++++++++++-
 9 files changed, 698 insertions(+), 52 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
