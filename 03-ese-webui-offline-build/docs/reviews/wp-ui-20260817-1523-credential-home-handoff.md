HANDOFF: WebUI credential-home isolation repair

INTENT
Review the unstaged follow-up to the approved WebUI baseline that redirects
home/profile environment variables away from host credential locations.

SCOPE
`bridge.py` only. The change preserves the allowlisted subprocess environment,
uses a bridge-owned scratch directory for HOME-like variables, and documents
that this is environment isolation rather than OS sandboxing.

TESTS
`python -m pytest -q` — 11 passed before this isolated bridge-only review
`git diff --check` — passed

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or engine
repository changes were used. Independent Codex review is required before
this follow-up is committed.
