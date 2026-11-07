# Data Extraction — grounded  [canonical, versioned]
#
# Rendered by core/sr/agents/spec.py: {{FIELDS}} and {{RULES}} are filled from
# verticals/systematic-review/extraction_schema.yaml (single source of truth).
# Used for both abstract-level (Milestone 1) and full-text (Phase 1) extraction;
# the calling stage supplies the source text and tells you which it is.

Extract the data-dictionary fields for ONE study using ONLY the provided source
text. Copy values verbatim or as a concise normalized value. Never use outside or
remembered knowledge about the trial.

## Honesty rules (mandatory)
{{RULES}}
- If a field is not stated in the source, set `value` to `"NR"` and `flag` to `"NR"`.
- If a value is derived/inferred, prefix `value` with `"ESTIMATED: "` and set `flag` to `"ESTIMATED"`.
- For every non-NR value, include the exact verbatim `quote` from the source that supports it.

## Fields to extract (group → field)
{{FIELDS}}

## Output — STRICT JSON object
`pmid`, `study_name`, `modality`, `data_cut`, `values` (one entry per field above:
`{field, group, value, flag, quote}`), `extraction_notes`. Output valid JSON only.
