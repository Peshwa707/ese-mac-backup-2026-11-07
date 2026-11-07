HANDOFF: WebUI offline queue work package

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: README.md, bridge.py, docs/UI_BUILD_LOOP.md, server.py, static/app.js, static/index.html, tests/test_webui.py

TESTS
`pytest -q` — 42 passed in 8.92s

DIFF STAT
```text
README.md             |  31 +++---
 bridge.py             |  22 +++++
 docs/UI_BUILD_LOOP.md |  22 +++--
 server.py             |  78 ++++++++++++---
 static/app.js         |  23 ++++-
 static/index.html     |  24 +++--
 tests/test_webui.py   | 263 +++++++++++++++++++++++++++++++++++++++++++++++---
 7 files changed, 411 insertions(+), 52 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
