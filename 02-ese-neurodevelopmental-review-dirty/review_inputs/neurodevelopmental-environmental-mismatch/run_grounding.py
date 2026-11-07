"""Bounded, multi-source grounding for the NDEM rapid-review modules.

This uses the canonical Evidence Synthesis Engine connectors and cite-or-omit
verification, but keeps each source bounded. It writes only public bibliographic
records; the personal case narrative is never sent to a literature source.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The shared project virtual environment is an editable install of the canonical
# main checkout. Force imports to resolve from the isolated review worktree that
# contains this script and its compiled verticals.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.connectors.clinicaltrials import ClinicalTrialsConnector
from core.connectors.europepmc import EuropePmcConnector
from core.connectors.pubmed import PubMedConnector
from core.sr.agent_io import dump_corpus, records_for_agents
from core.sr.config import load_sr_config
from core.sr.orchestrator.adapters import write_json
from core.sr.orchestrator.provenance import write_environment, write_search_manifest
from core.sr.pipeline import gather_corpus


MODULES = {
    "ndem-a-persistence-compensation": {"cap": 300},
    "ndem-b-overlap-social-dcd": {"cap": 300},
    "ndem-c-context-motivation-strengths": {"cap": 300},
    "ndem-d-external-scaffolding": {
        "cap": 300,
        "ct_query": "ADHD coaching organizational skills",
        "ct_cap": 100,
    },
    "ndem-e1-sleep-circadian": {
        "cap": 300,
        "ct_query": "ADHD bipolar sleep circadian",
        "ct_cap": 100,
    },
    "ndem-e2-mood-substances": {
        "cap": 350,
        "ct_query": "ADHD bipolar cannabis stimulant nicotine",
        "ct_cap": 100,
    },
    "ndem-f-daydreaming-rejection": {"cap": 250},
}


class BoundedQueryConnector:
    """Adapter that fixes both a source-specific query and a result cap."""

    def __init__(self, connector, *, query: str, cap: int):
        self.connector = connector
        self.source = connector.source
        self.query = query
        self.cap = cap

    async def search(self, query: str, structured: dict):
        return await self.connector.search(self.query, structured, max_records=self.cap)

    async def aclose(self):
        await self.connector.aclose()


def _europepmc_query(pubmed_query: str) -> str:
    """Translate the bounded PubMed query to a conservative Europe PMC query."""

    query = re.sub(
        r'\s+AND\s+\("1800/01/01"\[dp\]\s*:\s*"\d{4}/\d{2}/\d{2}"\[dp\]\)\s*$',
        "",
        pubmed_query,
    )
    query = re.sub(r"\[(?:tiab|Mesh|pt)\]", "", query, flags=re.IGNORECASE)
    return " ".join(query.split())


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _run_module(vertical: str, settings: dict, run_root: Path) -> dict:
    cfg = load_sr_config(vertical)
    cap = int(settings["cap"])
    epmc_query = _europepmc_query(cfg.pubmed_query)

    # Make source-specific queries visible to the engine search manifest.
    cfg.search["europepmc"] = {
        "executed": True,
        "query": epmc_query,
        "page_size": min(cap, 200),
    }

    sources = [
        PubMedConnector(retmax=cap),
        BoundedQueryConnector(
            EuropePmcConnector(page_size=min(cap, 200)),
            query=epmc_query,
            cap=cap,
        ),
    ]
    source_caps = {"pubmed": cap, "europepmc": cap}

    ct_query = settings.get("ct_query")
    if ct_query:
        ct_cap = int(settings.get("ct_cap", 100))
        cfg.search["clinicaltrials"] = {
            "executed": True,
            "query": ct_query,
            "page_size": min(ct_cap, 100),
        }
        sources.append(
            BoundedQueryConnector(
                ClinicalTrialsConnector(page_size=min(ct_cap, 100)),
                query=ct_query,
                cap=ct_cap,
            )
        )
        source_caps["clinicaltrials"] = ct_cap

    started = _timestamp()
    corpus = await gather_corpus(
        cfg,
        retmax=cap,
        sources=sources,
        close_sources=True,
    )

    run_dir = run_root / vertical
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_corpus(corpus, run_dir / "corpus.json")
    write_json(run_dir, "records_for_agents.json", records_for_agents(corpus))
    write_search_manifest(run_dir, corpus, timestamp=started)
    write_environment(run_dir, timestamp=started, repo_dir=Path.cwd())
    execution = {
        "vertical": vertical,
        "profile": "rapid",
        "started_utc": started,
        "completed_utc": _timestamp(),
        "source_caps": source_caps,
        "source_queries": {
            source: cfg.source_query(source) for source in source_caps
        },
        "prescreen_policy": (
            "No publication-type exclusion before Reviewer-1 screening because "
            "systematic reviews, meta-analyses, and guidelines are eligible evidence."
        ),
        "identified": len(corpus.identified),
        "deduped": len(corpus.deduped),
        "verified": len(corpus.verified),
        "rejected": len(corpus.rejected),
        "per_source": corpus.per_source,
        "sources_executed": corpus.sources_executed,
    }
    write_json(run_dir, "search_execution.json", execution)
    return execution


async def _main(args) -> int:
    selected = args.only or list(MODULES)
    unknown = sorted(set(selected) - set(MODULES))
    if unknown:
        raise SystemExit(f"Unknown module(s): {', '.join(unknown)}")

    if args.validate_only:
        rows = []
        for vertical in selected:
            cfg = load_sr_config(vertical)
            rows.append(
                {
                    "vertical": vertical,
                    "title": cfg.review_title,
                    "cap": MODULES[vertical]["cap"],
                    "pubmed_query": cfg.pubmed_query,
                    "europepmc_query": _europepmc_query(cfg.pubmed_query),
                    "clinicaltrials_query": MODULES[vertical].get("ct_query"),
                }
            )
        print(json.dumps(rows, indent=2))
        return 0

    args.run_root.mkdir(parents=True, exist_ok=True)
    summaries = []
    for vertical in selected:
        summary = await _run_module(vertical, MODULES[vertical], args.run_root)
        summaries.append(summary)
        print(json.dumps(summary, sort_keys=True))
    write_json(args.run_root, "grounding_summary.json", summaries)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("runs/ndem-rapid-20260729"),
    )
    parser.add_argument("--only", action="append", choices=sorted(MODULES))
    parser.add_argument("--validate-only", action="store_true")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
