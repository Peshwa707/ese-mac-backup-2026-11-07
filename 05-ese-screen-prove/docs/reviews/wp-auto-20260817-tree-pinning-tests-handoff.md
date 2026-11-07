HANDOFF: tree-pinning regression coverage

GOAL
Make the review-integrity behavior executable: capture-before-send ordering,
tree-pinned commit behavior, approval invalidation on tree changes, and
unlimited re-review after the former three-round threshold.

SCOPE
- Add deterministic unit tests for the PAI controller’s review-integrity
  helpers and critical approval/re-review branches.
- Keep engine scope and the offline-only boundary unchanged.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 75 passed
`python -m py_compile tools/pai_loop.py tests/test_pai_loop.py` — passed
`git diff --cached --check` — passed

EXIT CONDITION
Independent Codex review returns `VERDICT: APPROVE`; the reviewed tree and
parent still match; then the tree-pinned commit completes successfully.

SAFETY BOUNDARY
No live APIs, network calls, credentials, subscriptions, institutional access,
or external databases were used. The original ESE checkout remains untouched.
