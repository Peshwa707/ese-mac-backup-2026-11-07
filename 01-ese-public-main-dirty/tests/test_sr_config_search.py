"""Search-query construction — the spelling-robust variant_queries capability.

RCA fix (2026-07-10): exact-term PubMed queries miss records that misspell a drug
(e.g. 'remazolam' for 'remimazolam' — Tang 2023, PMID 37152102). A vertical may now
declare `pubmed.variant_queries`, which are OR'd into the executed query.
"""
from __future__ import annotations

from core.sr.config import SRConfig


def _cfg(search: dict) -> SRConfig:
    cfg = SRConfig.__new__(SRConfig)   # bypass yaml loading; exercise the property in isolation
    cfg.search = search
    return cfg


def test_variant_queries_are_or_expanded():
    cfg = _cfg({"pubmed": {"query": "remimazolam[tiab] AND hysteroscop*[tiab]",
                           "variant_queries": ["remazolam[tiab] AND hysteroscop*[tiab]"]}})
    assert cfg.pubmed_query == (
        "(remimazolam[tiab] AND hysteroscop*[tiab]) OR (remazolam[tiab] AND hysteroscop*[tiab])"
    )


def test_multiple_variants_all_or_combined():
    cfg = _cfg({"pubmed": {"query": "A", "variant_queries": ["B", "C"]}})
    assert cfg.pubmed_query == "(A) OR (B) OR (C)"


def test_no_variants_leaves_query_unchanged():
    cfg = _cfg({"pubmed": {"query": "remimazolam[tiab] AND hysteroscop*[tiab]"}})
    assert cfg.pubmed_query == "remimazolam[tiab] AND hysteroscop*[tiab]"


def test_empty_variant_entries_are_ignored():
    cfg = _cfg({"pubmed": {"query": "A", "variant_queries": ["", None, "B"]}})
    assert cfg.pubmed_query == "(A) OR (B)"


def test_whitespace_is_normalized():
    cfg = _cfg({"pubmed": {"query": "A   AND\n  B", "variant_queries": ["C\n\n  AND D"]}})
    assert cfg.pubmed_query == "(A AND B) OR (C AND D)"
