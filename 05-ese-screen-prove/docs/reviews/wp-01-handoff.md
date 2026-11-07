# WP-01 handoff: repository controls and frozen contract

## Intent

Establish the isolated repository boundary and implement deterministic hashing,
protocol locks, Task Envelope v1, and explicit data contracts for later stages.

## Scope

Included: repository controls, dual-review protocol wiring, package metadata,
canonical JSON, SHA-256 contracts, and model definitions. Deferred: import,
deduplication, screening, and replay behavior.

## Tests

Command: `pytest -q tests/test_contracts.py`

Initial result: `4 passed`; after review-driven corrections: `7 passed`.

## Review focus

Check that the lock and envelope fail closed on tampering, that canonical bytes
are stable, and that the repository does not accidentally add credentials,
remotes, or live connectors.

## Review response

Fable 5 returned `VERDICT: APPROVE` with four should-level findings. The follow-up
change kept Python 3.10 compatibility, removed the premature CLI entry point,
validated lock input hashes as lowercase SHA-256, rejected non-JSON NaN values,
and preserved typed defaults for imported references.
