# Evidence Synthesis Engine

A **domain-neutral** systematic review and meta-analysis engine. For any PICO it runs a fresh literature search, screens and extracts with honesty gates, pools statistics via R (metafor/netmeta), and exports a publication-grade manuscript — with the **human holding the final conclusion**.

Salvaged from [onco-evidence-engine](https://github.com/Peshwa707/onco-evidence-engine) (SR + stats only; case-analysis web app removed).

## Quick start

```bash
cd ~/Projects/evidence-synthesis-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Create a run from an existing vertical
ese init --vertical mm-bcma-early-relapse --profile systematic
# → prints runs/<id>

ese status runs/<id>
ese next runs/<id>          # run next pipeline stage
ese approve runs/<id> screen --actor "Dr. Smith"   # after honesty gate
ese export runs/<id> --approve-conclusions "Dr. Smith"  # fail-closed export
```

## Idea → protocol path

```bash
ese init --idea "BCMA CAR-T vs bispecific antibodies in early-relapse multiple myeloma" --profile systematic
ese next runs/<id>   # idea_to_pico → novelty → feasibility → …
```

## Honesty contract

- Cite-or-omit: every PMID live-verified or import-provenance-attested
- NR/ESTIMATED: never fabricated
- AI = Reviewer 1; human Reviewer 2 required (`--approve-conclusions`)
- Honesty gates on screen + extract: HMAC-signed human approval
- `ese export` calls `export_gate()` — blocked until all gates clear

## Layout

```
core/sr/           systematic review pipeline + orchestrator + stats
core/connectors/   PubMed, Europe PMC, ClinicalTrials, imports
core/sr/driver/    ese CLI (guided driver)
verticals/         per-review YAML configs
runs/              ephemeral run artifacts (gitignored)
```

## Tests

```bash
pytest    # 428+ offline tests
```

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Getting started](docs/GETTING_STARTED.md)
- [Honesty contract](docs/HONESTY_CONTRACT.md)

## Environment

| Variable | Purpose |
|----------|---------|
| `ESE_VERTICAL` | Active vertical (alias: `SR_VERTICAL`) |
| `ANTHROPIC_API_KEY` | LLM stages (screening, extraction) |
| `NCBI_API_KEY` | Optional PubMed rate limit |
| `ESE_RSCRIPT` | Path to Rscript (alias: `SR_RSCRIPT`) |
