"""Versioned stage specifications for the SR agent pipeline.

A StageSpec binds a repo prompt template + Pydantic output schema + pinned model +
batch size. ``render_prompt`` fills the template's placeholders from the vertical
config, so criteria/fields have a single source of truth (pico.yaml /
extraction_schema.yaml) — agents never improvise them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from core.sr.config import SRConfig
from core.sr.models import ExtractedStudy, ScreeningResult

def _prompt_path(prompt_file: str) -> Path:
    from core.sr.config import shared_or_vertical
    return shared_or_vertical(f"prompts/{prompt_file}")


@dataclass(frozen=True)
class StageSpec:
    name: str
    prompt_file: str
    item_schema: Type[BaseModel]   # schema for ONE output item
    model: str                     # pinned agent model tier
    batch_size: int
    output_pattern: str            # glob for this stage's output files
    backend: str = "anthropic"     # native-path backend hint (router selects by SR_LLM_PROFILE)

    def prompt_template(self) -> str:
        return _prompt_path(self.prompt_file).read_text(encoding="utf-8")

    def json_schema(self) -> dict:
        return self.item_schema.model_json_schema()

    def render_prompt(self, cfg: SRConfig) -> str:
        """Fill template placeholders from config (single source of truth)."""
        text = self.prompt_template()
        if self.name == "screening":
            inc = "\n".join(f"  - {c}" for c in cfg.inclusion_criteria)
            exc = "\n".join(f"  - {c}" for c in cfg.exclusion_criteria)
            text = text.replace("{{PICO}}", cfg.pico_text())
            text = text.replace("{{INCLUDE}}", inc)
            text = text.replace("{{EXCLUDE}}", exc)
        elif self.name in ("extraction", "fulltext_extraction"):
            fields = "\n".join(
                f"  - [{f['group']}] {f['name']}" for f in cfg.extraction_fields()
            )
            rules = "\n".join(f"- {r}" for r in cfg.extraction_rules)
            text = text.replace("{{FIELDS}}", fields)
            text = text.replace("{{RULES}}", rules)
        return text


# --- Registry (the canonical, versioned set of stages) ---------------------

SCREENING = StageSpec(
    name="screening",
    prompt_file="screening.md",
    item_schema=ScreeningResult,
    model="sonnet",
    batch_size=30,
    output_pattern="screen_batch_*.json",
)

EXTRACTION = StageSpec(
    name="extraction",
    prompt_file="extraction.md",
    item_schema=ExtractedStudy,
    model="sonnet",
    batch_size=1,
    output_pattern="extract_idx_*.json",
)

STAGES: dict[str, StageSpec] = {s.name: s for s in (SCREENING, EXTRACTION)}
