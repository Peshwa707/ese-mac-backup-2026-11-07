# Architecture

Three layers — driver, orchestrator, adapters.

```
ese CLI / Cursor skill
        ↓
core/sr/driver/     status · next · approve · export
        ↓
core/sr/orchestrator/engine.py   run_state.json · honesty gates · DAG
        ↓
Stage adapters      search · screen · extract · stats · manuscript …
        ↓
Connectors + R      PubMed · metafor · netmeta
```

## Stage DAG (systematic profile)

```
[idea_to_pico → novelty → feasibility → protocol_critic → autoconfig]  # optional front-end
  → search → prescreen → screen → fulltext → extract → rob → stats → grade
  → evidence_strength → assemble → manuscript
```

Profiles (`core/sr/profiles.yaml`) filter which stages run.

## Artifacts

All state under `runs/<id>/`:
- `run_state.json` — orchestrator source of truth
- `corpus.json`, `screening.json`, `extractions.json`, …
- `gates/` — signed honesty gate records

## Export

`ese export` requires:
1. `export_gate()` returns `(True, [])`
2. `--approve-conclusions "<Reviewer 2>"`

Never passes `gate=None` for orchestrated runs.
