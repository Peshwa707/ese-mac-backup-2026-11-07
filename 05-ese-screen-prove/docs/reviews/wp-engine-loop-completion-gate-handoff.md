# Engine autonomous-loop completion gate handoff

## Goal

Allow Claude/PAI to continue the isolated engine build through the remaining
offline contract and qualification work. The controller must not stop merely
because the historical pre-live readiness receipt exists.

## Scope

- `tools/pai_loop.py`
- `tests/test_pai_loop.py`
- `docs/AUTONOMOUS_BUILD_LOOP.md`
- This handoff file

The loop now stops only after an independently reviewed Git tree contains
`docs/reviews/ENGINE_OFFLINE_RECEIPT.md` with the exact marker
`ENGINE_OFFLINE_VERIFIED: true`; the controller then writes its own stop
receipt. The historical `READY_FOR_LIVE_API` receipt remains an engineering
boundary and never authorizes live APIs.

The queue adds current CLI-contract reconciliation and final engine offline
completion as bounded stages. No WebUI files, live connectors, credentials, or
network access are included.

## Verification

```text
python -m pytest -q tests/test_pai_loop.py
9 passed

python -m pytest -q
310 passed

python tools/pai_loop.py --once --dry-run
would invoke Claude for the first incomplete offline queue item
```

## Review gate

The independent Codex reviewer must approve the exact staged tree before this
controller change is committed or the worker is restarted.
