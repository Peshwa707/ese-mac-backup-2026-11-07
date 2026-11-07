HANDOFF: WebUI extraction/risk-of-bias artifact validation — fail-closed hash guard

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md (stage 6,
"Compatibility"): close a fail-open gap in this WebUI's own engine-artifact
contract check.

SCOPE
Files changed by Claude/PAI:
- server.py — `_validate_extraction_artifact`'s `record_hash` re-verification
  and `_validate_rob_artifact`'s `rob_hash` re-verification each now wrap
  their `_object_hash(body)` call in `try`/`except (TypeError, ValueError)`,
  matching the guard `_validate_synthesis_artifact`'s `calculation_hash`
  check already had. Without this guard, an engine-supplied artifact body
  containing a non-finite float (`NaN`/`Infinity`) in any unvalidated
  top-level field would raise an uncaught `TypeError`/`ValueError` from
  `_object_hash`'s `json.dumps(..., allow_nan=False)` instead of this
  function's own documented "fails closed (raises `BridgeError`)" contract.
- tests/test_webui.py — adds
  `test_validate_extraction_artifact_fails_closed_on_non_finite_hash_body`
  and `test_validate_rob_artifact_fails_closed_on_non_finite_hash_body`
  (both call `_validate_extraction_artifact`/`_validate_rob_artifact`
  directly, no bridge/stub/HTTP server, against a hand-written artifact that
  is otherwise valid but carries one extra top-level field set to
  `float("nan")`, asserting `BridgeError` is raised rather than
  `TypeError`/`ValueError`), and adds `_validate_extraction_artifact` and
  `_validate_rob_artifact` to the `from server import (...)` block so they
  can be unit-tested directly.

No other files are part of this work package. Prior same-day
`docs/reviews/wp-ui-auto-*-handoff.md` files are this loop's own generated
review-request history and are explicitly out of scope for this diff; they
are not part of the change being reviewed here and should not be staged with
it.

TESTS
`pytest -q` — 673 passed in 85.83s (0:01:25). This run was against the
working tree that already includes both new regression tests above, so the
673-pass count reflects them running and passing, not merely the
implementation.

DIFF STAT (server.py and tests/test_webui.py only)
```text
server.py           | 27 +++++++++++++--
tests/test_webui.py | ~125 +++++++++++++++++++++++++++++++++++++++++++++++++
2 files changed, ~150 insertions(+), 2 deletions(-)
```
(tests/test_webui.py count is an approximate line-range tally from direct
file inspection, not a `git diff --stat` output — no git/shell tool was
available in this session to compute an exact figure.)

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent review is required before
commit.
