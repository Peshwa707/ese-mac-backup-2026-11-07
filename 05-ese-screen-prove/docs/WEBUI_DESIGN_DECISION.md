# ESE WebUI design decision

Status: provisional baseline for the first implementation package.

The WebUI is the operator surface for ESE. It is not a Codex dashboard and it
must not require an AI agent to drive ordinary engine runs.

## Baseline workflows

- Protocol intake: guided protocol gate.
- Reference import and deduplication: manifest table with identity decisions.
- Screening: criteria-anchored triage queue, with human adjudication off by
  default and an explicit per-run toggle.
- Extraction: document-anchored extraction.
- Risk of bias: domain questionnaire.
- Synthesis: analysis-recipe builder.
- Audit and replay: timeline replay.
- Run management: stage-gated run board.

## First implementation boundary

The first UI package covers only the existing offline engine capabilities:
frozen reference import, deterministic deduplication, cached screening,
replay, and artifact inspection. It must run locally without network access,
credentials, live connectors, or subscriptions. Later UI phases may add the
remaining ESE engine stages, but each phase must preserve immutable artifacts,
provenance, fail-closed gates, and the pre-live hard stop.

This is a reversible product decision: later phases may replace a workflow
choice through a new versioned design decision without silently changing an
existing run.
