# Claude/PAI driver

`tools/pai_loop.py` is the smallest persistent implementer loop for this
repository. It invokes Claude through the local subscription/OAuth path with
`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` removed from its environment.

Claude receives only `Read`, `Edit`, and `Write` tools. The controller runs the
offline tests, stages the bounded diff, sends one handoff to the parked Codex
reviewer, and commits only after `VERDICT: APPROVE`.

The loop reads `docs/AUTONOMOUS_BUILD_LOOP.md`, advances one incomplete queue
item at a time, and stops when `docs/reviews/PRE_LIVE_READINESS_RECEIPT.md`
contains `READY_FOR_LIVE_API: true`. It must not be used to authorize live
APIs; that transition remains a separate explicit user decision.

Run one safe inspection cycle:

```text
tools/pai_loop.py --once --dry-run
```

The launchd label is
`com.neurologic.dual-review.evidence-synthesis-engine-screen-prove.pai`.
Stop it with:

```text
launchctl bootout gui/$(id -u)/com.neurologic.dual-review.evidence-synthesis-engine-screen-prove.pai
```
