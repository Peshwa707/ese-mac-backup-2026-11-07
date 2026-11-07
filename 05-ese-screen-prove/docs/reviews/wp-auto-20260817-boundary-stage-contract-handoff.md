HANDOFF: boundary contract correction and stage-governance update

GOAL
Make the live-provider/connector boundary honest and offline-first, while
giving the autonomous build a defined product goal and an explicit goal,
deliverable, verification, and exit condition for every engine stage.

SCOPE
- Remove the false claim that the in-process live gate is impossible to bypass.
- Keep the public `execute_live`/`fetch_live` methods default-deny unless the
  exact opt-in flag is present.
- Treat the private implementation hook as an ordinary in-process convention,
  not a security boundary.
- Remove the automatic three-review-round interruption from the PAI driver.
- Define the engine stage contract and queue in
  `docs/AUTONOMOUS_BUILD_LOOP.md`.

VERIFICATION
`/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q` — 71 passed
`git diff --cached --check` — passed

EXIT CONDITION
The package is committed only after an independent Codex reviewer returns
`VERDICT: APPROVE`. Review-round count remains recorded for auditability, but
does not stop the bounded package from being corrected and re-reviewed.

SAFETY BOUNDARY
No live APIs, network calls, credentials, subscriptions, institutional access,
or external databases were used. The repository remains separate from the
original ESE checkout. This package does not authorize live qualification.
