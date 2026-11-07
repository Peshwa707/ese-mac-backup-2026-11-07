HANDOFF: WebUI offline queue work package

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: README.md, bridge.py, docs/UI_BUILD_LOOP.md, server.py, static/app.js, static/index.html, static/styles.css, tests/test_webui.py

TESTS
`pytest -q` — 84 passed in 28.44s

DIFF STAT
```text
README.md             |  58 +++--
 bridge.py             |  26 ++
 docs/UI_BUILD_LOOP.md |  91 ++++---
 server.py             | 348 ++++++++++++++++++++++---
 static/app.js         |  86 +++++++
 static/index.html     |  31 ++-
 static/styles.css     |   3 +
 tests/test_webui.py   | 699 +++++++++++++++++++++++++++++++++++++++++++++++++-
 8 files changed, 1230 insertions(+), 112 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
