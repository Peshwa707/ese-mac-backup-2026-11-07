HANDOFF: WebUI offline queue work package — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: server.py

TESTS
`pytest -q` — 63 passed in 17.95s

DIFF STAT
```text
README.md                                          |  38 +-
 bridge.py                                          |  54 +++
 docs/UI_BUILD_LOOP.md                              |  53 ++-
 docs/reviews/wp-ui-auto-20260817-125454-handoff.md |  28 ++
 server.py                                          | 185 +++++++-
 static/app.js                                      |  27 +-
 static/index.html                                  |  27 +-
 store.py                                           |  41 +-
 tests/test_webui.py                                | 507 ++++++++++++++++++++-
 9 files changed, 874 insertions(+), 86 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
