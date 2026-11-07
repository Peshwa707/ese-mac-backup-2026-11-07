# WP-02 handoff: frozen import and deterministic deduplication

## Intent

Add bounded RIS/NBIB import that preserves the original export and its SHA-256
hash, then deduplicate publications deterministically with auditable reasons and
PRISMA-ready counts.

## Scope

Included: `screen_prove/imports.py`, `screen_prove/dedup.py`, and focused tests.
No live connectors, database searches, or full-text retrieval are included.

## Tests

Command: `pytest -q tests/test_import_and_dedup.py`

Initial result: `3 passed`; after review-driven corrections: `7 passed`.

## Review focus

Check RIS/NBIB field handling, raw-export preservation, stable record identifiers,
deduplication key precedence, and whether missing frozen hashes fail closed.

## Review response

The first review returned `VERDICT: CHANGES` for raw-path collision, NBIB date
normalization, and unsafe numeric RIS PMID inference. The implementation now
hash-addresses preserved raw files, normalizes four-digit years, accepts PMID
only from explicit C5 or labeled AN fields, handles NBIB LID DOI values, and
verifies the frozen corpus hash before deduplication.
