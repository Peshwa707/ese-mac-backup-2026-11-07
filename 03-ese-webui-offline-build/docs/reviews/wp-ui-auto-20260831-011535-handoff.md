HANDOFF: WebUI artifact hash-validation fail-closed fix — re-review (corrected)

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md (stage 6,
Compatibility): close a fail-open gap in the WebUI's own artifact-hash
re-verification. `_validate_extraction_artifact`'s `record_hash` check and
`_validate_rob_artifact`'s `rob_hash` check both call `_object_hash(body)`,
which serializes `body` with `allow_nan=False` (the engine's canonical hash
contract). A `body` containing a non-finite float (NaN/Infinity) in an
unvalidated top-level field would make `_object_hash` raise
`TypeError`/`ValueError`, escaping each function's own documented "fails
closed (raises BridgeError)" contract. `_validate_synthesis_artifact`'s
sibling `calculation_hash` check already guarded against this; the two
other validators did not.

CORRECTION NOTE (re-review of wp-ui-auto-20260831-011535)
The prior version of this handoff was rejected because its SCOPE/DIFF STAT
had drifted: intervening "re-review" cycles between
wp-ui-auto-20260831-002842 and wp-ui-auto-20260831-011535 each only listed
the immediately preceding handoff .md file as "changed," and their
regenerated DIFF STAT blocks silently dropped `tests/test_webui.py` even
though it was never modified again after
`docs/reviews/wp-ui-auto-20260831-002323-handoff.md` first added the two
regression tests below. The actual code (server.py) and regression tests
(tests/test_webui.py) were never lost — only this handoff's own bookkeeping
was wrong. This revision restores an accurate SCOPE/TESTS/DIFF STAT for the
real bounded diff and drops the accumulated self-referential doc-chain
entries from DIFF STAT, consistent with wp-ui-auto-20260831-002323's own
explicit statement that those prior handoff docs "are this loop's own
generated review-request history ... not part of the change being reviewed
here and should not be staged with it."

SCOPE (the complete bounded diff — only these two files)
- server.py:251 — `_validate_extraction_artifact`: wrap the
  `record_hash == _object_hash(body)` comparison in
  `try`/`except (TypeError, ValueError)` so a non-finite float anywhere in
  `body` is reported as a `BridgeError` ("record_hash does not match its
  content") instead of escaping as a bare exception.
- server.py:332 — `_validate_rob_artifact`: identical guard around the
  `rob_hash == _object_hash(body)` comparison.
- tests/test_webui.py:1610 —
  `test_validate_extraction_artifact_fails_closed_on_non_finite_hash_body`:
  calls `_validate_extraction_artifact` directly against a hand-written,
  otherwise-valid artifact with an extra `extra_engine_metadata: NaN` field
  and asserts `BridgeError` (not `TypeError`/`ValueError`) is raised,
  citing `record_hash`.
- tests/test_webui.py:1682 —
  `test_validate_rob_artifact_fails_closed_on_non_finite_hash_body`: same
  regression coverage for `_validate_rob_artifact`, citing `rob_hash`.
- tests/test_webui.py's `from server import (...)` block also imports
  `_validate_extraction_artifact` and `_validate_rob_artifact` directly so
  both can be unit-tested without a bridge/stub/HTTP server.

No other file is part of this work package. The accumulated
`docs/reviews/wp-ui-auto-*-handoff.md` files from earlier same-day
"re-review" cycles are stray review-request history, not part of this diff;
this agent has no file-deletion tool, so it cannot remove them, but the
controller should exclude them from staging so the committed change matches
this handoff's stated scope (server.py + tests/test_webui.py only).

STAGING (explicit, for the controller)
The prior re-review found that a staged diff included 25
`docs/reviews/*.md` files while `tests/test_webui.py` was left unstaged --
a staging-step error, not a change to the actual implementation or test
files, both of which were re-inspected during this revision and confirmed
unchanged from the SCOPE described above. This agent has no git/shell tool
in this session and therefore cannot itself run `git add`/`git status`/
`git diff --stat` or `pytest` to stage the diff or produce fresh output;
per this work package's own hard limits, "the local controller runs tests
and stages the exact diff." The controller must, before this package is
reviewed again:
  1. Run `git status`/`git diff --stat` against the working tree and stage
     exactly `server.py` and `tests/test_webui.py` -- no `docs/reviews/*.md`
     file (including this one) should be part of the staged diff.
  2. Run `pytest -q` against that exact staged working tree and attach the
     fresh output (pass/fail count and duration) to the next review request,
     rather than reusing any previously reported count.
  3. Attach the actual `git diff --stat` output for the two staged files in
     place of the approximate tally below.

TESTS
`pytest -q` — 673 passed in 85.83s (0:01:25), as recorded in
`docs/reviews/wp-ui-auto-20260831-002323-handoff.md`. That run was against a
working tree that already contained both new regression tests above, so the
673-pass count reflects them actually running and passing together with the
server.py guard, not merely the implementation existing. Neither server.py
nor tests/test_webui.py has been modified since that run — every
intervening handoff's own SCOPE section touched only a docs/reviews/*.md
file. This agent has no shell/test-execution tool available in this
session, so it cannot re-run pytest itself to produce fresh output; the
controller should re-run `pytest -q` against the current working tree to
reconfirm before staging (the previously reported "675 passed" baseline
cited in some intervening handoffs was not re-derived from an actual run of
this diff and should not be relied on in place of a fresh run).

DIFF STAT (server.py and tests/test_webui.py only)
```text
server.py           | 27 +++++++++++++--
tests/test_webui.py | ~125 +++++++++++++++++++++++++++++++++++++++++++++++++
2 files changed, ~150 insertions(+), 2 deletions(-)
```
(tests/test_webui.py count is an approximate line-range tally from direct
file inspection — lines 1610-1731 of the current file — not a
`git diff --stat` output; no git/shell tool is available in this session to
compute an exact figure. The controller should regenerate an exact
`git diff --stat` when staging.)

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
