HANDOFF: disputed-review fail-closed gate

GOAL
Preserve a human decision point only when the implementer explicitly disputes
an independent review finding, while keeping ordinary CHANGES re-review
unlimited.

SCOPE
- Instruct Claude to emit `DISPUTE: <reason>` instead of making a speculative
  edit when a review finding is genuinely incorrect or contract-changing.
- Detect that signal, persist the evidence, and pause for human adjudication.
- Add regression coverage for the dispute path and its fail-closed behavior.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 76 passed
`python -m py_compile tools/pai_loop.py tests/test_pai_loop.py` — passed
`git diff --cached --check` — passed

EXIT CONDITION
Independent Codex review returns `VERDICT: APPROVE`; no disputed finding is
active; the reviewed tree and parent still match before the tree-pinned commit.

SAFETY BOUNDARY
Offline only. No live APIs, network calls, credentials, subscriptions,
institutional access, or external databases were used. The original ESE
checkout remains untouched.
