HANDOFF: fixed canary fixtures and rejection coverage

GOAL
Make the public canary specifications genuinely bounded by pinning their
identifiers and payloads to versioned offline fixtures.

SCOPE
- PubMed canary pinned to PMID `39551604`.
- ClinicalTrials.gov canary pinned to fixture NCT ID `NCT00000000`.
- LLM canary pinned to record ID `fixture-record-v1` and title
  `Fixture screening record v1`.
- Reject non-fixture identifiers or titles before any live boundary call.
- Update deterministic envelope tests to vary only an explicitly constructed
  description, not a second live target.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 103 passed
`git diff --check` — passed
`git diff --cached --check` — passed

EXIT CONDITION
Independent Codex review returns `VERDICT: APPROVE`; no live implementation is
added; the exact reviewed tree is the only tree eligible for commit.

SAFETY BOUNDARY
Offline only. No network, credentials, databases, subscriptions, or live
connector/provider calls were used.
