"""Bridge between the no-LLM grounding phase and an external orchestrator.

When the engine's own ANTHROPIC_API_KEY path is unavailable, the LLM screening
and extraction stages can be supplied by an external orchestrator (e.g. Claude
Code harness agents). These helpers serialize the verified corpus to JSON for the
orchestrator, expose the minimal record set agents screen against, and load the
orchestrator's JSON results back into the typed models so ``build_run`` can
assemble the identical SRRun. The grounding guarantees still hold: only verified
PMIDs are present, and screening/extraction operate solely on these abstracts.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.models import Citation, RetrievalResult
from core.sr.config import SRConfig, load_sr_config
from core.sr.models import ExtractedStudy, ScreeningResult
from core.sr.pipeline import Corpus


def dump_corpus(corpus: Corpus, path: Path) -> Path:
    data = {
        "run_id": corpus.run_id,
        "search_result": corpus.search_result.model_dump(),
        "identified": [c.model_dump() for c in corpus.identified],
        "deduped": [c.model_dump() for c in corpus.deduped],
        "verified": [c.model_dump() for c in corpus.verified],
        "rejected": corpus.rejected,
        "seed_verification": corpus.seed_verification,
        "per_source": corpus.per_source,
        "sources_executed": corpus.sources_executed,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def load_corpus(path: Path, cfg: SRConfig | None = None) -> Corpus:
    cfg = cfg or load_sr_config()
    d = json.loads(path.read_text(encoding="utf-8"))
    return Corpus(
        cfg=cfg,
        run_id=d["run_id"],
        search_result=RetrievalResult(**d["search_result"]),
        identified=[Citation(**c) for c in d["identified"]],
        deduped=[Citation(**c) for c in d["deduped"]],
        verified=[Citation(**c) for c in d["verified"]],
        rejected=d["rejected"],
        seed_verification=d["seed_verification"],
        per_source=d.get("per_source"),
        sources_executed=d.get("sources_executed"),
    )


def records_for_agents(corpus: Corpus) -> list[dict]:
    """The minimal, grounded record set agents screen against (verified only)."""
    return [
        {
            "pmid": c.id,
            "title": c.title,
            "study_type": c.study_type or "",
            "year": c.year,
            "abstract": (c.snippet or "").strip() or "[no abstract available]",
        }
        for c in corpus.verified
    ]


def load_screening(items: list[dict]) -> list[ScreeningResult]:
    return [ScreeningResult(**it) for it in items]


def load_extractions(items: list[dict]) -> list[ExtractedStudy]:
    return [ExtractedStudy(**it) for it in items]
