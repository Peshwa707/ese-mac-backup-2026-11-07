# Seed decision review

## Decision

Seed this repository from the committed ESE tree at
`cbf829763c2b4b2740a32e23de360e0cf93f2334`, then implement only the Screen-and-
Prove slice. Do not seed from the dirty working tree and do not carry the full
pipeline into the active execution path.

## Independent architectural input

Claude Fable 5 recommended the clean committed slice over a fresh rewrite or a
full snapshot. Its reasoning was that the committed tree preserves tested
determinism, import, and provenance behavior, while a new isolated repository
limits coupling to deferred subsystems.

## Limits of this input

This is an architectural recommendation, not a software validation result or a
scientific conclusion. The repository's tests and manual crosstalk verdicts are
the acceptance evidence.
