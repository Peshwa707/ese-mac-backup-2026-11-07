HANDOFF: tree-pinned approval commit

GOAL
Close the review-to-commit race so an approval commits exactly the Git tree
that was captured before the review request.

SCOPE
- Capture the staged index tree and current parent before sending a review.
- Recheck both at approval time.
- On mismatch, invalidate the approval and automatically send a fresh review.
- On match, create the commit from the captured tree with `git commit-tree`
  and update the branch ref, rather than rereading a potentially changed index.
- Continue recording review rounds without an automatic cap.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 71 passed
`python -m py_compile tools/pai_loop.py` — passed
`git diff --cached --check` — passed

EXIT CONDITION
Independent Codex review returns `VERDICT: APPROVE`; the approved tree and
parent still match; the tree-pinned commit completes successfully.

SAFETY BOUNDARY
Offline only. No live APIs, network calls, credentials, subscriptions,
institutional access, or external databases were used. The original ESE
checkout remains untouched.
