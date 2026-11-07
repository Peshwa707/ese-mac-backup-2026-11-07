HANDOFF: WebUI offline queue work package

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: README.md, bridge.py, docs/UI_BUILD_LOOP.md, server.py, static/app.js, static/index.html, store.py, tests/test_webui.py

TESTS
`pytest -q` — 112 passed in 41.11s

DIFF STAT
```text
README.md             |  60 ++--
 bridge.py             |  49 ++++
 docs/UI_BUILD_LOOP.md |  96 +++++--
 server.py             | 423 ++++++++++++++++++++++++++---
 static/app.js         |  59 +++-
 static/index.html     |  35 ++-
 store.py              |   6 +-
 tests/test_webui.py   | 740 ++++++++++++++++++++++++++++++++++++++++++++++++--
 8 files changed, 1352 insertions(+), 116 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
