# Run-level extraction CLI handoff

## Goal

Expose the existing frozen extraction contract through one deterministic local
CLI command so the WebUI can later drive source-anchored extraction without
importing engine internals or enabling live access.

## Scope

- `screen_prove/cli.py`
- `tests/test_cli.py`
- This handoff file

The command accepts a UTF-8 frozen full-text file, a record identifier,
provenance filename, and a JSON field specification. Reported fields require
source offsets; `not_reported` and `estimated` fields use the existing bounded
constructors. The output is the existing hash-bound extraction-record-v1
artifact.

## Verification

```text
python -m pytest -q tests/test_cli.py
7 passed

python -m pytest -q
301 passed in 4.05s
```

The CLI rejects status-contradictory field inputs instead of silently
discarding offsets, values, or estimation metadata. This preserves the
fail-closed extraction contract and is covered by four regression cases.

No network, credentials, model calls, database connectors, or changes to the
original ESE checkout were used.

## Deferred

Risk-of-bias and synthesis CLI surfaces remain separate bounded packages.
