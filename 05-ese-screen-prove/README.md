# ESE Screen-and-Prove

This is an isolated, offline-first increment of the Evidence Synthesis Engine.
It freezes a published-review reference corpus, deduplicates it deterministically,
runs one cached screening adapter, preserves `abstain`, compares against sealed
published labels, and verifies replay integrity.

It does not search live databases, retrieve full text, call subscription models,
extract study outcomes, pool statistics, or write a manuscript.

## Repository control

The project was seeded from committed ESE `HEAD`
`cbf829763c2b4b2740a32e23de360e0cf93f2334`. See
[`docs/REPOSITORY_CONTROL.md`](docs/REPOSITORY_CONTROL.md). The current ESE
checkout and its dirty working tree are not part of this repository.

Implementation branch: `codex/ai-only-screen-prove`.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Every work package follows the manual dual-review protocol in
`.dual-review/PROTOCOL.md`. The evidence workflow's human-adjudication toggle is
separate from the engineering review gate.
