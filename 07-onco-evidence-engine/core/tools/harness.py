"""Agent-mediated harness backend for subscription LLM work.

The harness backend never calls an API. It writes a model-neutral request into a
run folder, waits for a human-operated Claude/GPT agent to write schema-valid
JSON results there, then resumes through the normal tool artifact contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.sr.agents.runner import collect, prepare_batches
from core.sr.agents.spec import EXTRACTION, SCREENING, StageSpec
from core.sr.config import SRConfig

HARNESS_SOURCES = {
    "claude": "Claude (PAI subscription)",
    "gpt": "GPT (ChatGPT Pro / Codex)",
}

_META_FILE = "harness_stage_models.json"
_RESULT_PREFIX = {"screening": "screen_batch", "extraction": "extract_idx"}
_TOOL_STAGE = {"screen": ("screen", SCREENING), "extract": ("extract", EXTRACTION)}


@dataclass
class HarnessWorkPending(Exception):
    tool: str
    stage: str
    source: str
    request_path: Path
    expected_output: str
    instruction: str

    def __str__(self) -> str:
        return self.instruction


def harness_source(args: dict) -> str:
    source = str(args.get("harness_source") or "claude").lower()
    if source not in HARNESS_SOURCES:
        raise ValueError(f"unknown harness source {source!r}; choose one of {', '.join(HARNESS_SOURCES)}")
    return source


def source_label(source: str) -> str:
    return HARNESS_SOURCES[source]


def tool_stage(tool: str) -> tuple[str, StageSpec]:
    return _TOOL_STAGE[tool]


def harness_dir(run_dir: Path, tool: str, source: str) -> Path:
    stage_dir, _ = tool_stage(tool)
    return run_dir / "harness" / stage_dir / source


def expected_result_names(spec: StageSpec, n_batches: int) -> list[str]:
    prefix = _RESULT_PREFIX[spec.name]
    return [f"{prefix}_{i:03d}.json" for i in range(n_batches)]


def _request_text(
    spec: StageSpec,
    cfg: SRConfig,
    source: str,
    batch_paths: list[Path],
    expected: list[str],
) -> str:
    schema = json.dumps(spec.json_schema(), indent=2)
    prompt = spec.render_prompt(cfg)
    inputs = "\n".join(f"- `{p.relative_to(p.parents[1])}`" for p in batch_paths)
    outputs = "\n".join(f"- `{name}`" for name in expected)
    return f"""# Harness Work Request: {spec.name}

Source: {source_label(source)}

This request is model-neutral. A Claude/PAI agent or GPT/Codex/ChatGPT agent can
complete it by reading the input files here and writing JSON result files back to
this same directory.

## Inputs
{inputs or "- (no inputs)"}

## Write Results Here
Write these exact result files in this directory:

{outputs or "- (no outputs)"}

For screening batches, each result file must contain a JSON list of screening
objects for that batch. For extraction batches, each result file must contain
one extracted-study JSON object.

## Non-Negotiable Honesty Rules
- Use only the provided records and prompt.
- Cite-or-omit: never invent a PMID, study, number, quote, or field.
- If a value is missing, mark it `NR`; do not guess.
- If uncertain during screening, use `decision: uncertain`.
- Return JSON matching the schema exactly.

## Prompt
```text
{prompt}
```

## JSON Schema
```json
{schema}
```
"""


def prepare_request(
    run_dir: Path,
    tool: str,
    source: str,
    cfg: SRConfig,
    items: list[dict],
) -> tuple[Path, list[str]]:
    hdir = harness_dir(run_dir, tool, source)
    hdir.mkdir(parents=True, exist_ok=True)
    _, spec = tool_stage(tool)
    batches = prepare_batches(spec, items, hdir, cfg)
    expected = expected_result_names(spec, len(batches))
    request = hdir / "REQUEST.md"
    request.write_text(_request_text(spec, cfg, source, batches, expected), encoding="utf-8")
    return request, expected


def pending(
    run_dir: Path,
    tool: str,
    source: str,
    request_path: Path,
    expected: list[str],
) -> HarnessWorkPending:
    pattern = ", ".join(expected) if expected else "(no result files expected)"
    instr = (
        f"AWAITING AGENT: awaiting harness agent ({source}) for '{tool}': "
        f"fill {request_path}, write {pattern} in {request_path.parent}, then re-run."
    )
    stage_dir, _ = tool_stage(tool)
    return HarnessWorkPending(tool, stage_dir, source, request_path, pattern, instr)


def missing_result_files(run_dir: Path, tool: str, source: str, expected: list[str]) -> list[str]:
    hdir = harness_dir(run_dir, tool, source)
    return [name for name in expected if not (hdir / name).exists()]


def collect_results(
    run_dir: Path,
    tool: str,
    source: str,
    expected_ids: list[str],
):
    _, spec = tool_stage(tool)
    return collect(spec, harness_dir(run_dir, tool, source),
                   expected_ids=expected_ids, drop_unexpected=True,
                   missing_screening_as_uncertain=(tool == "screen"))


def record_stage(run_dir: Path, stage: str, source: str) -> None:
    entry = {
        "stage": stage,
        "model": source_label(source),
        "backend": "harness",
        "n_calls": 1,
    }
    entries = read_stage_entries(run_dir)
    key = (entry["stage"], entry["model"], entry["backend"])
    merged: dict[tuple[str, str, str], dict[str, Any]] = {
        (e.get("stage"), e.get("model"), e.get("backend")): dict(e) for e in entries
    }
    merged[key] = entry
    (run_dir / _META_FILE).write_text(json.dumps(list(merged.values()), indent=2),
                                      encoding="utf-8")


def read_stage_entries(run_dir: Path) -> list[dict]:
    p = run_dir / _META_FILE
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def merge_stage_entries(*groups: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str, str], dict] = {}
    for group in groups:
        for entry in group:
            key = (entry.get("stage"), entry.get("model"), entry.get("backend"))
            if key in merged:
                merged[key]["n_calls"] = max(
                    int(merged[key].get("n_calls", 1)), int(entry.get("n_calls", 1)))
            else:
                merged[key] = dict(entry)
    return list(merged.values())


def replay_stage_entries(entries: list[dict]) -> None:
    """Replace the current context stage log with the merged harness-aware entries."""
    from core.llm import client

    client.reset_stage_log()
    for entry in entries:
        stage = entry.get("stage")
        model = entry.get("model")
        backend = entry.get("backend")
        if not stage or not model or not backend:
            continue
        for _ in range(max(1, int(entry.get("n_calls", 1)))):
            client._record_stage(str(stage), str(model), str(backend))
