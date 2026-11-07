"""Build per-source queries from structured case fields + vertical strategies.

Generic engine: all term knowledge (MeSH terms, synonyms, brand<->generic maps,
per-source query templates) comes from the vertical's ``search_strategies.yaml``.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9+\-]{2,}")

# Characters that break search APIs when embedded in query terms.
_STRIP_CHARS_RE = re.compile(r'[%:()"\']+')

# Tokens that are pure measurement noise: a bare integer/float, or a token that
# is nothing but digits/punctuation with an optional trailing unit suffix.
# Examples that should be dropped: "10%", "10", "1L", "2.5mg".
_NOISE_TOKEN_RE = re.compile(r"^\d[\d.,]*(%|[a-z]{1,4})?$", re.IGNORECASE)


def _sanitize_term(term: str) -> str:
    """Return a clean, API-safe query term.

    Rules (all generic — no domain-specific knowledge here):
    1. Strip characters that break search APIs: ``%``, ``:``, quotes, parens.
    2. Collapse whitespace.
    3. For multi-token terms, truncate at the first bare measurement-noise token
       (bare numbers or number+unit patterns like "10", "10%", "1L", "2.5mg").
       This keeps the leading marker token(s) and drops trailing quantifiers.
       Example: "MARKER region 19 deletion" → "MARKER region" (stops before "19").
       Example: "MARKER score 10%" → the quantifier is dropped; leading
               non-noise tokens are kept for the caller to use.
    4. If the result after stripping is a single pure-noise token, return "".
    """
    # Step 1: strip breaking characters.
    cleaned = _STRIP_CHARS_RE.sub(" ", term)
    # Step 2: collapse whitespace.
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""

    # Step 3: truncate at the first noise token in a multi-token term.
    tokens = cleaned.split()
    if len(tokens) > 1:
        # Find the first noise token and cut there.
        cut = len(tokens)
        for i, tok in enumerate(tokens):
            if _NOISE_TOKEN_RE.match(tok):
                cut = i
                break
        tokens = tokens[:cut]
        if not tokens:
            return ""
        cleaned = " ".join(tokens)

    # Step 4: a single remaining token that is pure noise → empty.
    if len(cleaned.split()) == 1 and _NOISE_TOKEN_RE.match(cleaned):
        return ""

    return cleaned


def _expand_terms(value: str, synonyms: dict[str, list[str]]) -> list[str]:
    """Return the value plus any configured synonyms / brand<->generic variants.

    Matching strategy:
    - Exact case-insensitive key/variant equality (original behaviour).
    - Substring: if the value *contains* the synonym key (or a variant), expand
      with the full canonical synonym group. This lets a longer, more specific
      field value match a shorter canonical key when the key's phrase is a
      substring of the value (shared root present in both).
    """
    out = [value]
    low = value.lower()
    for key, variants in synonyms.items():
        key_low = key.lower()
        variant_lows = [v.lower() for v in variants]
        # Exact match on key or any variant (original rule).
        exact = key_low == low or low in variant_lows
        # Substring match: the canonical key appears inside the value, or the
        # value appears inside the canonical key, or any variant does likewise.
        substring = (
            key_low in low
            or low in key_low
            or any(v in low for v in variant_lows)
            or any(low in v for v in variant_lows)
        )
        if exact or substring:
            out.extend([key, *variants])
    return list(dict.fromkeys(t for t in out if t))


def build_queries(structured: dict[str, Any], strategies: dict[str, Any]) -> dict[str, list[str]]:
    """Return {source_name: [query, ...]} for every configured source.

    Term collection strategy:
    - For each configured key field, sanitize and expand each value.
    - To prevent a single field's synonym explosion from monopolising all query
      slots, collect at most ``max_per_field`` representatives from each field's
      expansion (default 1). The canonical/sanitized form always comes first;
      synonyms follow and fill any remaining global ``max_terms`` budget.
    - This ensures e.g. one field with many synonym variants cannot crowd out
      another field's lead term when max_terms is small.
    """
    synonyms: dict[str, list[str]] = strategies.get("synonyms", {})
    key_fields: list[str] = strategies.get("key_fields", list(structured.keys()))
    # max_per_field: how many terms each field's expansion may contribute as
    # "priority" slots before extras are appended at the end.
    max_per_field: int = strategies.get("max_per_field", 1)

    # Phase 1: collect one representative per field value (the canonical/sanitized
    # form) so every field is represented in the output.
    priority_terms: list[str] = []
    extra_terms: list[str] = []

    for field in key_fields:
        value = structured.get(field)
        if not value:
            continue
        values = value if isinstance(value, list) else [value]
        for v in values:
            sanitized = _sanitize_term(str(v))
            if not sanitized:
                continue
            expanded = _expand_terms(sanitized, synonyms)
            # Split into priority (first max_per_field) and extras.
            priority_terms.extend(expanded[:max_per_field])
            extra_terms.extend(expanded[max_per_field:])

    # Merge: priority first, then extras; dedup while preserving order.
    all_terms = list(dict.fromkeys(
        t for t in (priority_terms + extra_terms)
        if _TOKEN_RE.search(t) and not _NOISE_TOKEN_RE.fullmatch(t.strip())
    ))

    queries: dict[str, list[str]] = {}
    sources: dict[str, Any] = strategies.get("sources", {})
    for source, cfg in sources.items():
        template: str = cfg.get("template", "{terms}")
        joiner: str = cfg.get("joiner", " AND ")
        max_terms: int = cfg.get("max_terms", len(all_terms))
        anchor: str = cfg.get("anchor", "")
        chosen = all_terms[:max_terms]
        if not chosen:
            continue
        joined = joiner.join(chosen)
        query = template.format(terms=joined)
        if anchor:
            query = f"{query} {anchor}"
        queries[source] = [query]
    return queries
