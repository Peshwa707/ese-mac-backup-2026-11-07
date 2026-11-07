HANDOFF: WebUI offline queue work package — exception-handling fail-closed regression coverage

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md. Close the
exception-handling gap identified in independent review: `_object_hash(body)`
(called from `_validate_extraction_artifact` and `_validate_rob_artifact` in
server.py) serializes `body` with `allow_nan=False` and raises
`TypeError`/`ValueError` if `body` contains a non-finite float (NaN/Infinity)
anywhere among its unvalidated top-level fields. Without a guard, that
exception would escape these two validators' own documented "Fails closed
(raises BridgeError)" contract, instead of being reported as the
artifact-content mismatch it actually is. `_validate_synthesis_artifact`
already guards its equivalent `calculation_hash` check with
`try/except (TypeError, ValueError)`; this package adds the matching guard
to `_validate_extraction_artifact`'s `record_hash` check (server.py:241-254)
and `_validate_rob_artifact`'s `rob_hash` check (server.py:327-335), plus
direct-unit-test regression coverage for both.

SCOPE
Files changed by Claude/PAI:
- server.py — wrap the `_object_hash(body)` re-verification in
  `_validate_extraction_artifact` and `_validate_rob_artifact` in
  `try/except (TypeError, ValueError)` so a non-finite float in an
  unvalidated top-level field is reported as `BridgeError`, not an
  uncaught `TypeError`/`ValueError`.
- tests/test_webui.py — add
  `test_validate_extraction_artifact_fails_closed_on_non_finite_hash_body`
  and `test_validate_rob_artifact_fails_closed_on_non_finite_hash_body`,
  which call `_validate_extraction_artifact`/`_validate_rob_artifact`
  directly (no bridge/stub/HTTP server) against a hand-written,
  otherwise-valid artifact carrying one extra non-finite-float field, and
  assert `BridgeError` (citing `record_hash`/`rob_hash`) is raised.

TESTS
`pytest -q` — to be run by the local controller prior to staging (this
agent has no test-execution tool). No test files or fixtures outside
tests/test_webui.py were modified.

DIFF STAT
Scope is limited to the two files listed above: a small guard added to each
of `_validate_extraction_artifact` and `_validate_rob_artifact` in
server.py, and two new regression tests (with explanatory docstrings) added
to tests/test_webui.py. No other source, test, or doc file was modified by
this task.

KNOWN ISSUE (not fixed by this task, flagged for the controller)
A prior automated review-loop iteration left 21 stray
`docs/reviews/wp-ui-auto-20260830-*-handoff.md` and
`docs/reviews/wp-ui-auto-20260831-000307..004533-handoff.md` files sitting
in the working tree from earlier "re-review" cycles that recursively
re-listed each other in their own DIFF STAT sections. Those files are
unrelated to this task's scope (server.py fail-closed guard +
regression tests) and were not created, edited, or deleted by this task —
this agent has no file-deletion tool available. The controller should
exclude those 21 files from the staged diff (or remove them) so the
committed change matches this handoff's stated scope.

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent review is required before
commit.
