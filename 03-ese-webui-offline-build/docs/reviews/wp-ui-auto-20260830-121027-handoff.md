HANDOFF: WebUI offline queue work package — re-review — re-review

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md.

SCOPE
Files changed by Claude/PAI: 

TESTS
`pytest -q` — 673 passed in 86.19s (0:01:26)

DIFF STAT
```text
docs/reviews/wp-ui-auto-20260830-120421-handoff.md | 21 +++++++++++++++++
 docs/reviews/wp-ui-auto-20260830-120709-handoff.md | 22 ++++++++++++++++++
 server.py                                          | 27 ++++++++++++++++++++--
 3 files changed, 68 insertions(+), 2 deletions(-)
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
