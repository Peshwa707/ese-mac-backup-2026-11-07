"""Per-stage gate summaries for the ese driver."""

from __future__ import annotations

import json
from pathlib import Path


def summarize_stage(run_dir: Path, stage_name: str) -> str:
    run_dir = Path(run_dir)
    if stage_name == "screen":
        p = run_dir / "screening.json"
        if not p.exists():
            return "Screening not run yet."
        data = json.loads(p.read_text(encoding="utf-8"))
        decisions: dict[str, int] = {}
        for row in data:
            d = row.get("decision", "unknown")
            decisions[d] = decisions.get(d, 0) + 1
        return f"Screening: {len(data)} records — decisions: {decisions}"
    if stage_name == "extract":
        p = run_dir / "extractions.json"
        if not p.exists():
            return "Extraction not run yet."
        data = json.loads(p.read_text(encoding="utf-8"))
        return f"Extraction: {len(data)} studies extracted — spot-check quotes before approving."
    if stage_name == "idea_to_pico":
        p = run_dir / "pico.json"
        if not p.exists():
            return "PICO draft not ready."
        pico = json.loads(p.read_text(encoding="utf-8"))
        return (
            f"PICO draft — population: {pico.get('population', '')[:120]}\n"
            f"  intervention: {pico.get('intervention', '')[:120]}\n"
            f"  outcomes: {', '.join(pico.get('outcomes', [])[:5])}"
        )
    return f"Stage {stage_name!r} — review artifacts under {run_dir}"
