HANDOFF: WebUI offline queue work package — re-review (fix pass)

INTENT
Bounded offline work package selected from docs/UI_BUILD_LOOP.md stage 4
("screening"), specifically the human-adjudication blocking queue (the
"offline queue"). Test-only change; no server, bridge, or static UI source
was modified.

PRIOR REVIEW
An independent review of the previous staged diff under this same
timestamp found: (1) this handoff claimed a 99-line `tests/test_webui.py`
change that was not actually present in the staged diff, and (2) the
staged diff also contained an unrelated `wp-ui-auto-20260827-203845-
handoff.md` change that this handoff's own diff stat did not account for.
Both are blockers about the staged diff not matching the claims made
about it, not about test content. This is the fix pass for both: the
`tests/test_webui.py` change described below is now actually present, and
`wp-ui-auto-20260827-203845-handoff.md` has been rewritten as a short
superseded notice pointing at this file (no file-delete tool is available
in this environment, so it cannot be removed outright).

SCOPE
Files changed by Claude/PAI:
- tests/test_webui.py (two new tests; see below)
- docs/reviews/wp-ui-auto-20260827-203845-handoff.md (rewritten as a
  superseded notice, see PRIOR REVIEW above)
- docs/reviews/wp-ui-auto-20260827-204209-handoff.md (this file, rewritten)

`tests/test_webui.py` changes, added alongside the existing stage-4
blocking-queue tests, entirely offline through the deterministic stub
bridge (`_install_stub_bridge`) every other test in this file already
uses — no new server-side code path is exercised, only existing
`screening`/`adjudicate`/`human_adjudication` behavior already implemented
in server.py:
- `test_screening_route_reports_empty_state_before_any_screen_has_run`:
  GET `/api/runs/<id>/screening` on a run that has never been screened
  returns the safe all-empty state (`decisions`, `abstained_ids`,
  `remaining` all empty, `adjudications` `{}`, `blocked` `false`,
  `human_adjudication` `"off"`) rather than erroring. Every other existing
  test in this file only ever calls this route after `screen` has already
  run at least once.
- `test_adjudicate_updates_decision_for_already_resolved_id_while_still_blocked`:
  calling `adjudicate` a second time for an id this run already resolved,
  while the `screen` stage is still `blocked` on a different remaining id,
  overwrites the earlier recorded decision (verified via both the
  `screening` route and the on-disk `adjudications.json`) instead of being
  rejected or silently ignored — distinct from the existing
  `test_adjudicate_partial_queue_keeps_stage_blocked`, which only ever
  adjudicates each id once.

TESTS
Not executed by Claude/PAI in this fix pass: this environment's available
tools have no shell/subprocess access to run `pytest` directly. Per this
task's operating constraints, the local controller runs the test suite
against the actual staged diff and stages it; no test-run result is
asserted here that was not actually observed.

DIFF STAT (approximate; controller's staged diff is authoritative)
```text
tests/test_webui.py                                         | 106 ++++++++++
docs/reviews/wp-ui-auto-20260827-203845-handoff.md           |  11 ++--
docs/reviews/wp-ui-auto-20260827-204209-handoff.md           |  ~60 ++++----
```

HARD STOP
No live APIs, credentials, subscriptions, institutional access, or changes to
the engine repositories were used. Independent Codex review is required before
commit.
