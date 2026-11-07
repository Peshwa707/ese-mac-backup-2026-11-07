HANDOFF: review-integrity and reachable-stage correction

GOAL
Ensure an approval can commit only the exact staged diff that was reviewed,
and make every defined offline engine stage reachable before the live-API stop.

SCOPE
- Hash the exact staged binary patch when a review request is sent.
- Compare that hash at approval time; if it changed, invalidate approval and
  automatically request a fresh review.
- Preserve continuous re-review without an automatic three-round stop.
- Correct the queue hard-stop text so stages 1 through 8 can complete before
  the live-API boundary.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 71 passed
`python -m py_compile tools/pai_loop.py` — passed
`git diff --cached --check` — passed

EXIT CONDITION
Commit only after the independent Codex reviewer returns `VERDICT: APPROVE`
and the staged-diff hash still matches the reviewed hash.

SAFETY BOUNDARY
Offline only. No live APIs, network calls, credentials, subscriptions,
institutional access, or external databases were used. The original ESE
checkout remains untouched.
