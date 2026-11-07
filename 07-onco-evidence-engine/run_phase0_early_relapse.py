"""Phase 0 — Grounding/search for the mm-bcma-early-relapse review.

Live PubMed + Europe PMC + ClinicalTrials.gov -> cross-source dedup -> PMID/DOI verify.
No LLM / API key needed. Writes corpus.json + records_for_agents.json into runs/<id>/.
"""
import asyncio, json
from pathlib import Path

from core.sr.config import load_sr_config
from core.sr.pipeline import gather_corpus
from core.sr.agent_io import dump_corpus, records_for_agents
from core.connectors.pubmed import PubMedConnector
from core.connectors.europepmc import EuropePmcConnector
from core.connectors.clinicaltrials import ClinicalTrialsConnector


async def main():
    cfg = load_sr_config("mm-bcma-early-relapse")
    sources = [
        PubMedConnector(retmax=cfg.retmax),
        EuropePmcConnector(),
        ClinicalTrialsConnector(),
    ]
    corpus = await gather_corpus(cfg, sources=sources)

    run_dir = Path("runs") / cfg.short_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_corpus(corpus, run_dir / "corpus.json")
    records = records_for_agents(corpus)
    (run_dir / "records_for_agents.json").write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8"
    )

    print("=== PHASE 0 SEARCH COMPLETE ===")
    print("per-source identified:", json.dumps(corpus.per_source or {}, indent=2))
    print("sources executed:", corpus.sources_executed)
    print("identified (raw):", len(corpus.identified))
    print("after dedup:", len(corpus.deduped))
    print("after verification (PMID/DOI):", len(corpus.verified))
    print("rejected (failed verify):", len(corpus.rejected) if corpus.rejected else 0)
    print("named-trial seed check:", json.dumps(corpus.seed_verification or {}, indent=2))
    print("records_for_agents (to screen):", len(records))
    print("run dir:", run_dir.resolve())


if __name__ == "__main__":
    asyncio.run(main())
