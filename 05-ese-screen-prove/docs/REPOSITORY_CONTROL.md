# Repository control

## Seed provenance

- Source repository: `/Users/neurologic/Projects/evidence-synthesis-engine`
- Source commit: `cbf829763c2b4b2740a32e23de360e0cf93f2334`
- Source state used: committed tree only
- Deliberately excluded: dirty change to `core/tools/sr_io.py` and the untracked
  `docs/superpowers/plans/2026-08-17-standalone-pilot-workflow.md`
- Remote: none configured for this local project

## Ownership and change control

The implementation agent may edit this repository. The independent crosstalk
reviewer is read-only and returns a verdict; it does not edit or commit code.
The implementation loop may continue re-reviewing a bounded package until it
converges; the review-round count is retained for auditability.

Every commit must identify its work package and have:

1. a test-first diff;
2. fresh test output;
3. a staged handoff under `docs/reviews/`; and
4. a matching `VERDICT: APPROVE` record.

No remote, credentials, live connector, subscription harness, or institutional
access may be added without a separate user request.
