# Claude Science workflow learnings for ESE

These are workflow observations to translate into testable ESE behavior. They
are not validation of Claude Science, its connectors, or any scientific result.

## Patterns to preserve

1. **Workspace-level operating instructions**

   Keep protocol-scoped instructions persistent and versioned. They should
   travel with the run manifest and be visible before execution, rather than
   existing only in a chat session.

2. **Connector capability visibility**

   Show whether a connector supports metadata, abstract, full text, source
   links, or only an acquisition request. Do not infer full-text retrieval from
   a journal search or citation result.

3. **Source-first responses**

   Every engine output should separate source-reported fields, calculated
   values, and model/operator inference. Missing values remain `NR`; calculated
   values are labeled `ESTIMATED` with the calculation.

4. **Bounded retrieval and access states**

   Represent `metadata_only`, `abstract_available`, `full_text_available`,
   `paywalled`, `login_required`, `institutional_access_required`, and
   `unresolved` as explicit states. An access state must never be silently
   upgraded by an LLM.

5. **Trial and publication identity warnings**

   Preserve publication-level records while warning when multiple reports may
   describe one trial population. Do not merge them without an explicit,
   auditable identity decision.

6. **Structured, bounded connector tests**

   Connector checks should use a known identifier, return only requested
   fields, preserve the request/response hashes, and verify that identifiers,
   titles, DOI, and links describe the same publication or record.

7. **Reasoning and uncertainty controls**

   Record provider/model/reasoning settings when an LLM is used, but keep model
   output advisory. The engine must stop on insufficient evidence and must not
   turn a persuasive answer into a treatment conclusion.

## Integration rule

Each pattern becomes an engine schema, invariant, or offline replay test before
it becomes a WebUI feature or a live connector. The WebUI consumes these
versioned contracts; it does not define them.
