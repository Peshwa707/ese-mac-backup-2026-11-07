# Run-level single-study synthesis CLI handoff

## Goal

Expose the existing bounded synthesis calculation contract through one
deterministic local CLI command. The command accepts one frozen 2x2 count table
and one supported effect measure, then writes the existing verifiable
`synthesis-calculation-v1` artifact.

## Scope

- `screen_prove/cli.py`
- `tests/test_cli.py`
- This handoff file

The `synthesize` command performs one protocol-named calculation for one study
record. It does not pool studies, infer missing counts, apply continuity
corrections, or access external services. Undefined ratio calculations remain
`insufficient_data` with explicit uncertainty flags. Unsupported fields are
rejected rather than silently discarded.

## Verification

```text
python -m pytest -q tests/test_cli.py
12 passed

python -m pytest -q
306 passed
```

No network, credentials, model calls, database connectors, or changes to the
original ESE checkout were used.

## Deferred

Cross-study pooling and any live evidence retrieval remain separate, gated
work. The WebUI must not present this single-study calculation as a
meta-analysis.
