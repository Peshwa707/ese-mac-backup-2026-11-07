# Harness Backend

The harness backend lets `screen` and `extract` run through a human-operated
subscription assistant without giving the tool CLI API credentials. The CLI
writes a model-neutral request, exits with an `AWAITING AGENT` message, and
resumes after the expected JSON result files appear.

## Claude / PAI Handoff

Run the tool with `--harness --harness-source claude`. The CLI writes:

```text
harness/<stage>/claude/REQUEST.md
```

A Claude Code or PAI agent reads that `REQUEST.md`, reads the referenced batch
input files in the same harness directory, and writes the exact result filenames
listed in the request back into that same directory. Re-run the same tool command
after all expected files are present.

## GPT / Codex Handoff

Run the tool with `--harness --harness-source gpt`. The CLI writes:

```text
harness/<stage>/gpt/REQUEST.md
```

Paste the full `REQUEST.md` into a Codex, ChatGPT Pro, or GPT session. Save the
assistant's JSON outputs as the exact filenames listed in the request, in the
same `harness/<stage>/gpt/` directory. Re-run the same tool command after all
expected files are present.

## Guardrails

- The harness is enabled only for `screen` and `extract`.
- Cloud-only tools, including claim-check, cannot be forced to harness.
- Screening omissions are marked `uncertain`; hallucinated PMIDs are dropped.
- Missing whole result files keep the tool pending until every expected batch is
  present.
- Resume is idempotent: completed harness stages record one provenance entry per
  stage and source.
