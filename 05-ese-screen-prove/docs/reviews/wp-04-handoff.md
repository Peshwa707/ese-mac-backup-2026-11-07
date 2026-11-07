# WP-04 handoff: sealed comparison, replay, and CLI

## Intent

Complete the offline workflow with sealed published-label comparison, package
hashing, replay verification, and a small CLI covering init, lock, import, dedup,
screen, package, compare, and replay.

## Scope

Included: `screen_prove/evaluation.py`, `screen_prove/replay.py`,
`screen_prove/cli.py`, package metadata, and tests. No live network, model, or
subscription integration is included.

## Tests

Command: `pytest -q`

Initial result: `27 passed`; after review-driven corrections: `30 passed`.

## Review focus

Check that published labels remain sealed until comparison, package hashes and
cache/envelope chains fail closed on tampering, replay is byte-stable, and the
CLI does not broaden scope beyond the offline workflow.

## Review response

The first review returned `VERDICT: CHANGES` because CLI comparison bypassed
package-hash verification. The corrected package verifies hashes before compare,
replay requires exact record coverage and lock/input binding, re-sealing an
existing package is safe, non-object sealed-label artifacts fail with
`IntegrityError`, and a `seal` CLI command makes the sealed-label step usable.

“Sealed” means tamper-evident and excluded from screening inputs in this offline
slice; the labels remain plaintext and are not cryptographically secret.
