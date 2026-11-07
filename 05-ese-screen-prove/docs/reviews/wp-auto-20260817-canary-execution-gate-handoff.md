HANDOFF: canary execution-boundary validation

GOAL
Enforce the fixed canary fixture invariant on the gated execution path, not
only while constructing a request description.

SCOPE
- Validate the complete `LiveRequestDescription` in each canary hook.
- Reject directly constructed non-fixture PMID, NCT, record-ID, and title
  descriptions even when the opt-in flag is set.
- Keep the actual live hooks unimplemented and offline-only.
- Add authorized-path rejection tests for all three canaries.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 106 passed
`git diff --check` — passed
`git diff --cached --check` — passed

EXIT CONDITION
Independent Codex review returns `VERDICT: APPROVE`; no live implementation,
credential access, or network behavior is introduced.
