HANDOFF: continuous-review protocol alignment

GOAL
Align the local dual-review protocol, role instructions, controller, and tests
with the explicitly authorized continuous-review workflow.

SCOPE
- Remove the fixed three-round escalation rule from the repository-local
  dual-review protocol and PAI/Claude role files.
- Preserve fresh review after every change, independent approval before commit,
  tree-pinned commit integrity, and escalation for genuinely disputed findings
  or explicit human redirection.
- Keep the stage queue and engine scope unchanged.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 75 passed
`python -m py_compile tools/pai_loop.py tests/test_pai_loop.py` — passed
`rg -n "after 3|three.*round|Bounded loops|3 CHANGES|unconverged CHANGES" .dual-review docs tools tests` — only historical handoff/test-name references remain
`git diff --cached --check` — passed

EXIT CONDITION
Independent Codex review returns `VERDICT: APPROVE`; the reviewed tree and
parent still match; then the tree-pinned commit completes successfully.

SAFETY BOUNDARY
Offline only. No live APIs, network calls, credentials, subscriptions,
institutional access, or external databases were used. The original ESE
checkout remains untouched.
