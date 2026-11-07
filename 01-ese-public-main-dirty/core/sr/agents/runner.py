"""Prepare stage inputs and validate/repair stage outputs — pure Python, tested.

This is the deterministic half of the agent pipeline: it splits work into batch
input files, renders the canonical prompt from config, then collects the agent
outputs and validates every item against the stage's Pydantic schema. Items that
are missing or schema-invalid are returned as a repair list so the caller can
re-run only those — the same self-healing pattern we ran by hand, made systematic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from core.sr.agents.spec import StageSpec
from core.sr.config import SRConfig


@dataclass
class CollectResult:
    valid: list[Any] = field(default_factory=list)      # validated schema objects
    missing: list[str] = field(default_factory=list)    # ids not produced / invalid (repair these)
    n_files: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing


def prepare_batches(
    spec: StageSpec, items: list[dict], run_dir: Path, cfg: Optional[SRConfig] = None
) -> list[Path]:
    """Split items into batch input files and write the rendered prompt."""
    in_dir = run_dir / f"{spec.name}_in"
    in_dir.mkdir(parents=True, exist_ok=True)
    if cfg is not None:
        (run_dir / f"{spec.name}_prompt.md").write_text(spec.render_prompt(cfg), encoding="utf-8")
    batches: list[Path] = []
    for k, start in enumerate(range(0, len(items), spec.batch_size)):
        chunk = items[start : start + spec.batch_size]
        p = in_dir / f"batch_{k:03d}.json"
        p.write_text(json.dumps(chunk, indent=2), encoding="utf-8")
        batches.append(p)
    return batches


def collect(
    spec: StageSpec,
    run_dir: Path,
    *,
    id_field: str = "pmid",
    expected_ids: Optional[list[str]] = None,
    drop_unexpected: bool = False,
    missing_screening_as_uncertain: bool = False,
) -> CollectResult:
    """Load stage outputs, validate each against the schema, report repairs."""
    files = sorted(run_dir.glob(spec.output_pattern))
    valid: list[Any] = []
    seen: set[str] = set()
    produced: set[str] = set()
    invalid: list[str] = []
    expected = {str(i) for i in expected_ids or []}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            invalid.append(f.name)
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            item_id = str(it.get(id_field)) if isinstance(it, dict) and it.get(id_field) is not None else ""
            if item_id:
                produced.add(item_id)
            try:
                obj = spec.item_schema.model_validate(it)
            except ValidationError:
                invalid.append(item_id or f.name)
                continue
            obj_id = getattr(obj, id_field, None)
            if drop_unexpected and expected_ids is not None and str(obj_id) not in expected:
                continue
            valid.append(obj)
            if obj_id is not None:
                seen.add(str(obj_id))
    missing: list[str] = []
    if expected_ids is not None:
        missing = [str(i) for i in expected_ids if str(i) not in seen]
    if missing_screening_as_uncertain and spec.name == "screening":
        from core.sr.models import ScreenDecision, ScreeningResult

        omitted = [i for i in missing if i not in produced]
        for pmid in omitted:
            valid.append(ScreeningResult(
                pmid=pmid,
                decision=ScreenDecision.UNCERTAIN,
                reason="Harness agent omitted this expected PMID; marked uncertain for review.",
                confidence=0.0,
            ))
        missing = [i for i in missing if i not in omitted]
    return CollectResult(valid=valid, missing=missing + invalid, n_files=len(files))
