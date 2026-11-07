"""Consistency backbone for the SR agent stages.

Each LLM stage (screen, extract, …) is a versioned StageSpec: a repo prompt
template (rendered from config), a Pydantic output schema, a pinned model, and a
batch size. The runner prepares batch inputs and validates/repairs outputs in
pure Python (tested); the actual model calls run via Claude Max harness agents
(driven from the conversation) or, when a key is set, the native api_executor —
same spec either way. This keeps the *process* repo-defined and reproducible even
though execution uses harness agents.
"""
