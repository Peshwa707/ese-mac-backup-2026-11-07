HANDOFF: WebUI offline queue work package

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: README.md, bridge.py, docs/UI_BUILD_LOOP.md, server.py, static/app.js, static/index.html, store.py, tests/test_webui.py

TESTS
`pytest -q` — 63 passed in 17.94s

DIFF STAT
```text
README.md             |  38 ++--
 bridge.py             |  54 ++++++
 docs/UI_BUILD_LOOP.md |  53 ++++--
 server.py             | 154 +++++++++++++--
 static/app.js         |  27 ++-
 static/index.html     |  27 ++-
 store.py              |  41 +++-
 tests/test_webui.py   | 507 ++++++++++++++++++++++++++++++++++++++++++++++++--
 8 files changed, 817 insertions(+), 84 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
