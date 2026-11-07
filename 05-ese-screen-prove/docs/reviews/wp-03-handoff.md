# WP-03 handoff: cached screening and adjudication toggle

## Intent

Implement one offline deterministic screening adapter with request/response cache
integrity, Task Envelope v1 provenance, explicit include/exclude/abstain output,
and the locked human-adjudication mode boundary.

## Scope

Included: `screen_prove/screening.py` and focused screening tests. The adapter is
an offline fixture; no provider credentials, live model calls, or database access
are included.

## Tests

Command: `pytest -q tests/test_screening.py`

Initial result: `5 passed`; after review-driven corrections: `21 passed` across
the contract, import/dedup, and screening suites.

## Review focus

Check cache tamper detection, lock/input binding, allowed decision vocabulary,
preservation of abstentions, and the distinction between AI-only output and the
engineering crosstalk gate.

## Review response

Fable 5 returned `VERDICT: APPROVE`. Before commit, the implementation added a
post-dedup `records_hash` checked by screening, routed package construction
through the typed screening dataclasses, and normalized malformed cache files to
`IntegrityError`.
