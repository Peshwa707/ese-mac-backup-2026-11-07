"""Unit tests for core/grounding/query_expansion.py.

All tests run offline — no live API calls are made. The representative case is:
  diagnosis: "non-small cell lung adenocarcinoma"
  biomarkers: ["EGFR exon 19 deletion", "PD-L1 TPS 10%"]
  stage: "IV"           <- present in structured data but NOT in key_fields

Assertions verify:
  (a) No built query for any source contains '%', ':' inside term values, or
      literal parentheses that originated from term values.
  (b) The PubMed query contains a treatment anchor and is NOT a 4-way AND of
      raw fields (stage / prior_lines must not appear).
  (c) 'EGFR' and an NSCLC / 'non-small cell lung' variant appear in the queries.
  (d) 'PD-L1 TPS 10%' does not appear verbatim (reduced to 'PD-L1').
"""

from __future__ import annotations

import re

import pytest
import yaml

from core.grounding.query_expansion import _sanitize_term, build_queries

# ---------------------------------------------------------------------------
# Load the real oncology strategies (no mocking — we test the config too).
# ---------------------------------------------------------------------------
_STRATEGIES_PATH = (
    __file__.replace("tests/test_query_expansion.py", "")
    + "verticals/oncology/search_strategies.yaml"
)


@pytest.fixture(scope="module")
def strategies():
    import pathlib
    path = pathlib.Path(__file__).parent.parent / "verticals" / "oncology" / "search_strategies.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def representative_case():
    return {
        "diagnosis": "non-small cell lung adenocarcinoma",
        "biomarkers": ["EGFR exon 19 deletion", "PD-L1 TPS 10%"],
        "stage": "IV",
        "prior_lines": "1",
    }


@pytest.fixture(scope="module")
def built_queries(representative_case, strategies):
    return build_queries(representative_case, strategies)


# ---------------------------------------------------------------------------
# _sanitize_term unit tests
# ---------------------------------------------------------------------------

class TestSanitizeTerm:
    def test_strips_percent(self):
        assert "%" not in _sanitize_term("PD-L1 TPS 10%")

    def test_strips_colon(self):
        assert ":" not in _sanitize_term("some:value")

    def test_strips_parens(self):
        result = _sanitize_term("something (extra)")
        assert "(" not in result
        assert ")" not in result

    def test_strips_quotes(self):
        result = _sanitize_term('"EGFR mutation"')
        assert '"' not in result

    def test_pd_l1_tps_reduces_to_marker(self):
        """'PD-L1 TPS 10%' -> keep leading marker token, drop quantifier."""
        result = _sanitize_term("PD-L1 TPS 10%")
        # The numeric noise token '10' (from '10%' after strip) must be gone.
        assert "10" not in result
        # The marker name should survive.
        assert "PD-L1" in result

    def test_egfr_exon_deletion_keeps_gene(self):
        """'EGFR exon 19 deletion' -> trailing '19' noise dropped, EGFR kept."""
        result = _sanitize_term("EGFR exon 19 deletion")
        assert "EGFR" in result
        # '19' is a bare number and should be dropped as a trailing noise token.
        assert "19" not in result

    def test_clean_term_unchanged(self):
        assert _sanitize_term("NSCLC") == "NSCLC"

    def test_empty_after_strip_returns_empty(self):
        assert _sanitize_term("10%") == ""

    def test_collapses_whitespace(self):
        result = _sanitize_term("  EGFR   mutation  ")
        assert "  " not in result


# ---------------------------------------------------------------------------
# build_queries integration tests (real YAML config + real logic)
# ---------------------------------------------------------------------------

class TestBuildQueries:

    # (a) No query for any source contains '%', ':', or raw parentheses from terms.
    def test_no_percent_in_any_query(self, built_queries):
        for source, qlist in built_queries.items():
            for q in qlist:
                assert "%" not in q, f"'%' found in {source} query: {q!r}"

    def test_no_colon_inside_term_values(self, built_queries):
        """Colons are legal in openFDA field selectors (field:value) but must not
        appear *inside* a term value itself."""
        for source, qlist in built_queries.items():
            for q in qlist:
                # openFDA template legitimately has 'indications_and_usage:"..."'
                # so we only ban bare colons not preceded by a field name word.
                # Simplest rule: after removing the known FDA field prefix, no ':'
                # should remain.
                stripped = re.sub(r'indications_and_usage:', '', q)
                assert ":" not in stripped, (
                    f"Unexpected ':' inside term value in {source} query: {q!r}"
                )

    def test_no_literal_parens_from_terms(self, built_queries, strategies):
        """Parentheses originating from term *values* must not appear in any query.

        Anchors (appended after the formatted template) may legitimately contain
        parens for boolean grouping (e.g. ``AND (treatment OR therapy OR trial)``).
        We verify parens only in the term-generated portion, not in the anchor.
        """
        sources_cfg = strategies.get("sources", {})
        for source, qlist in built_queries.items():
            anchor = sources_cfg.get(source, {}).get("anchor", "")
            for q in qlist:
                # Strip the anchor suffix to isolate the term-only portion.
                term_portion = q[: -len(anchor)].rstrip() if anchor and q.endswith(anchor) else q
                assert "(" not in term_portion, (
                    f"'(' from term values found in {source} query term portion: {term_portion!r}"
                )
                assert ")" not in term_portion, (
                    f"')' from term values found in {source} query term portion: {term_portion!r}"
                )

    # (b) PubMed query has treatment anchor and is NOT a 4-way AND.
    def test_pubmed_has_treatment_anchor(self, built_queries):
        assert "pubmed" in built_queries
        pubmed_q = built_queries["pubmed"][0]
        assert re.search(r"treatment|therapy|trial", pubmed_q, re.IGNORECASE), (
            f"PubMed query missing treatment anchor: {pubmed_q!r}"
        )

    def test_pubmed_not_four_way_and(self, built_queries):
        """PubMed must not AND 4 or more raw fields (stage/prior_lines excluded)."""
        pubmed_q = built_queries["pubmed"][0]
        # Count AND conjunctions between actual terms (not the anchor AND).
        # The anchor adds one AND; the core terms should be joined by at most 1 AND.
        # We check that stage IV and prior_lines don't appear.
        assert "stage" not in pubmed_q.lower(), (
            f"'stage' leaked into PubMed query: {pubmed_q!r}"
        )
        assert "prior" not in pubmed_q.lower(), (
            f"'prior_lines' leaked into PubMed query: {pubmed_q!r}"
        )
        assert " IV " not in pubmed_q and not pubmed_q.endswith(" IV"), (
            f"Raw stage value 'IV' leaked into PubMed query: {pubmed_q!r}"
        )

    # (c) EGFR and an NSCLC / non-small cell lung variant appear.
    def test_egfr_appears_in_some_query(self, built_queries):
        all_queries = " ".join(q for qlist in built_queries.values() for q in qlist)
        assert "EGFR" in all_queries, (
            f"'EGFR' not found in any query. All queries: {built_queries}"
        )

    def test_nsclc_variant_appears_in_some_query(self, built_queries):
        all_queries = " ".join(q for qlist in built_queries.values() for q in qlist).lower()
        has_nsclc = "nsclc" in all_queries or "non-small cell lung" in all_queries or "non small cell lung" in all_queries
        assert has_nsclc, (
            f"No NSCLC variant found in any query. All queries: {built_queries}"
        )

    # (d) 'PD-L1 TPS 10%' must not appear verbatim in any query.
    def test_pd_l1_tps_not_verbatim(self, built_queries):
        for source, qlist in built_queries.items():
            for q in qlist:
                assert "PD-L1 TPS 10%" not in q, (
                    f"Verbatim 'PD-L1 TPS 10%' leaked into {source} query: {q!r}"
                )
                assert "10%" not in q, (
                    f"'10%' leaked into {source} query: {q!r}"
                )

    # Additional sanity checks.
    def test_all_four_sources_present(self, built_queries):
        assert set(built_queries.keys()) == {"pubmed", "clinicaltrials", "openfda", "pdq"}

    def test_openfda_uses_quoted_phrase(self, built_queries):
        """openFDA query must use indications_and_usage:"..." not indications_and_usage:(...)."""
        openfda_q = built_queries["openfda"][0]
        assert openfda_q.startswith('indications_and_usage:"'), (
            f"openFDA query not using quoted phrase format: {openfda_q!r}"
        )

    def test_clinicaltrials_no_percent(self, built_queries):
        ct_q = built_queries["clinicaltrials"][0]
        assert "%" not in ct_q, f"'%' in ClinicalTrials query: {ct_q!r}"


# ---------------------------------------------------------------------------
# Synonym matching tests
# ---------------------------------------------------------------------------

class TestSynonymExpansion:

    def test_substring_match_fires_for_nsclc_variant(self, strategies):
        """A diagnosis containing 'non-small cell lung' triggers NSCLC synonym group."""
        from core.grounding.query_expansion import _expand_terms
        synonyms = strategies["synonyms"]
        result = _expand_terms("non-small cell lung adenocarcinoma", synonyms)
        joined = " ".join(result).lower()
        assert "nsclc" in joined, f"NSCLC not in expansion: {result}"

    def test_egfr_synonym_expansion(self, strategies):
        """'EGFR exon deletion' (after sanitization) expands to include EGFR synonyms."""
        from core.grounding.query_expansion import _expand_terms, _sanitize_term
        synonyms = strategies["synonyms"]
        sanitized = _sanitize_term("EGFR exon 19 deletion")
        result = _expand_terms(sanitized, synonyms)
        joined = " ".join(result)
        assert "EGFR" in joined, f"EGFR not in expansion: {result}"
