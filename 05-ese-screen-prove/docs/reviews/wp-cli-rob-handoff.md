# Run-level risk-of-bias CLI handoff

## Goal

Expose the existing bounded risk-of-bias assessment contract through one
deterministic local CLI command. The command accepts a frozen JSON assessment
specification and writes the existing hash-bound `rob-assessment-v1` artifact;
it performs no inference, network access, or live database work.

## Scope

- `screen_prove/cli.py`
- `tests/test_cli.py`
- This handoff file

The `rob` command requires `record_id`, `tool`, `domains`, and
`overall_judgment`. Domain judgments remain bounded to the existing contract,
including explicit `not_assessed` states and mandatory rationales. Unsupported
top-level or domain fields are rejected rather than silently discarded.

## Verification

```text
python -m pytest -q tests/test_cli.py
9 passed

python -m pytest -q
303 passed
```

No network, credentials, model calls, database connectors, or changes to the
original ESE checkout were used.

## Deferred

Synthesis CLI surfaces remain a separate bounded package.
