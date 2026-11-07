"""Native-path executor — runs SR stages headlessly via core/llm/client.

This is the documented one-flip migration from harness agents (Claude Max) to the
engine's own Anthropic SDK path: it requires ANTHROPIC_API_KEY but needs no
conversation/harness. It delegates to the SAME stage functions the engine already
uses (screen_batch / extract_study), so the StageSpec, prompts, and schemas are
identical to the agent path — only the executor changes. Until a key is set, the
harness-agent path remains the default.
"""

from __future__ import annotations

import os

from core.models import Citation, IdType
from core.sr.config import SRConfig, load_sr_config
from core.sr.models import ExtractedStudy, ScreeningResult


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _to_citation(rec: dict) -> Citation:
    return Citation(
        id_type=IdType.PMID,
        id=str(rec["pmid"]),
        source="pubmed",
        title=rec.get("title", ""),
        snippet=rec.get("abstract", ""),
        study_type=rec.get("study_type") or None,
        year=rec.get("year"),
    )


def run_screening(records: list[dict], cfg: SRConfig | None = None) -> list[ScreeningResult]:
    """Native screening (requires ANTHROPIC_API_KEY).

    Applies the deterministic publication-type pre-screen first: editorials/letters
    are excluded by type and conference abstracts routed to the sensitivity bucket
    before any LLM screening (PRISMA 2.2.3/2.2.4).
    """
    from core.sr.screen import screen_batch
    from core.sr.prescreen import prescreen_records

    cfg = cfg or load_sr_config()
    to_screen, _report = prescreen_records(records)
    return screen_batch([_to_citation(r) for r in to_screen], cfg)


def run_extraction(record: dict, cfg: SRConfig | None = None) -> ExtractedStudy:
    """Native extraction of one study (requires ANTHROPIC_API_KEY)."""
    from core.sr.extract import extract_study

    cfg = cfg or load_sr_config()
    return extract_study(_to_citation(record), cfg)
