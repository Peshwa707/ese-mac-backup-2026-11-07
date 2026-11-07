HANDOFF: WebUI offline operator foundation

GOAL
Give users a local operator surface for the existing offline ESE increment,
without requiring Codex or another agent to drive ordinary runs.

SCOPE
- Local Python standard-library server and static operator shell.
- Explicit CLI bridge to the isolated Screen-and-Prove engine.
- Run creation, engine initialization, artifact listing, and offline health
  status.
- Stage-gated endpoints for import, deduplication, protocol lock, screening,
  package, replay, and doctor.
- Human adjudication remains off by default with an explicit per-run toggle.
- No live APIs, credentials, subscriptions, institutional access, or changes
  to the original ESE checkout.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 3 passed
`git diff --check` — passed

EXIT CONDITION
Independent Codex reviewer returns `VERDICT: APPROVE` before the first UI
commit. Later UI stages remain separate work packages and must consume the
versioned engine artifact contract.
