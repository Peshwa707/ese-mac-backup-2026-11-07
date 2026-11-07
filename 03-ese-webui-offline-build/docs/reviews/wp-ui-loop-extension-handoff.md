# WebUI autonomous-loop extension handoff

## Goal

Continue the reviewed offline WebUI build after the existing compatibility
milestone so Claude/PAI can implement extraction, risk of bias, synthesis, and
final full-stack qualification before any live-API work is considered.

## Scope

- `tools/pai_loop.py`
- `tests/test_pai_loop.py`
- `docs/UI_BUILD_LOOP.md`
- This handoff file
- Pre-existing untracked `tools/codex` is excluded.

## Contract change

- `UI_COMPATIBILITY_RECEIPT.md` remains an intermediate milestone.
- The controller now stops only when the exact reviewed tree contains
  `docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md` with the exact marker
  `FULL_STACK_OFFLINE_VERIFIED: true`.
- The prompt and queue now identify offline phases for extraction, risk of
  bias, synthesis, and final full-stack qualification.
- No live APIs, credentials, network access, subscriptions, or institutional
  systems are enabled by this change.

## Verification

```text
python -m pytest -q tests/test_pai_loop.py
19 passed
git diff --check
passed
```

## Review focus

- Confirm the old compatibility receipt cannot trigger the final stop.
- Confirm the final marker is checked from the exact reviewed Git tree.
- Confirm the queue makes extraction/RoB/synthesis the next bounded offline
  work and preserves the live-API hard stop.
