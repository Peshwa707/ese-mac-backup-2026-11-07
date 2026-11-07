# Current CLI contract reconciliation handoff

## Goal

Make the current extraction, risk-of-bias, and single-study synthesis CLI
surfaces explicit in the engine/WebUI contract and qualification receipt.

## Scope

- `screen_prove/cli.py`
- `screen_prove/cli_contract.py`
- `screen_prove/contract.py`
- `screen_prove/qualification.py`
- `tests/test_cli_contract.py`
- `tests/test_contract.py`
- `tests/test_qualification.py`
- This handoff file

The package drives `extract`, `rob`, and `synthesize` through the real CLI
entry point, preserves the original hash-bound artifacts after verification,
and verifies the resulting command-surface bundle and tamper behavior. It
does not add pooling, network access, credentials, or live connectors.

## Verification

```text
python -m pytest -q tests/test_cli_contract.py tests/test_qualification.py
40 passed

python -m pytest -q
330 passed
```

## Review gate

The exact staged tree requires independent Codex approval before commit. The
engine PAI worker remains stopped until that approval is received.
