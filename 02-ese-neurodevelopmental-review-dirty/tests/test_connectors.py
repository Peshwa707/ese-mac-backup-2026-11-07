import httpx
import pytest
import respx

from core.connectors.base import dedup
from core.connectors.clinicaltrials import ClinicalTrialsConnector
from core.connectors.guidelines import PdqConnector
from core.connectors.openfda import OpenFdaConnector
from core.connectors.pubmed import PubMedConnector
from core.models import Citation, IdType
from tests.conftest import (
    CTGOV_ONE,
    CTGOV_STUDIES,
    OPENFDA_LABEL,
    PUBMED_EFETCH_XML,
    PUBMED_ESEARCH,
    PUBMED_ESUMMARY,
)


@respx.mock
async def test_pubmed_search_and_verify():
    respx.get(url__regex=r"esearch\.fcgi").mock(
        return_value=httpx.Response(200, json=PUBMED_ESEARCH)
    )
    respx.get(url__regex=r"efetch\.fcgi").mock(
        return_value=httpx.Response(200, text=PUBMED_EFETCH_XML)
    )
    respx.get(url__regex=r"esummary\.fcgi").mock(
        return_value=httpx.Response(200, json=PUBMED_ESUMMARY)
    )
    conn = PubMedConnector()
    result = await conn.search("lung", {})
    assert result.available
    assert {c.id for c in result.citations} == {"111", "222"}
    rct = next(c for c in result.citations if c.id == "111")
    assert "randomized" in (rct.study_type or "").lower()

    assert await conn.verify(Citation(id_type=IdType.PMID, id="111", source="pubmed")) is True
    await conn.aclose()


@respx.mock
async def test_pubmed_rejects_unverifiable_id():
    respx.get(url__regex=r"esummary\.fcgi").mock(
        return_value=httpx.Response(200, json={"result": {"uids": []}})
    )
    conn = PubMedConnector()
    ok = await conn.verify(Citation(id_type=IdType.PMID, id="999999", source="pubmed"))
    assert ok is False
    await conn.aclose()


@respx.mock
async def test_clinicaltrials_search_and_verify():
    respx.get(url__regex=r"/studies\?").mock(
        return_value=httpx.Response(200, json=CTGOV_STUDIES)
    )
    respx.get(url__regex=r"/studies/NCT").mock(
        return_value=httpx.Response(200, json=CTGOV_ONE)
    )
    conn = ClinicalTrialsConnector()
    result = await conn.search("osimertinib", {})
    assert result.citations[0].id == "NCT01234567"
    assert await conn.verify(result.citations[0]) is True
    await conn.aclose()


@respx.mock
async def test_openfda_search():
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=OPENFDA_LABEL)
    )
    conn = OpenFdaConnector()
    result = await conn.search("osimertinib", {})
    c = result.citations[0]
    assert c.id == "abc-set-id"
    assert c.id_type == IdType.URL
    assert "Tagrisso" in c.title
    await conn.aclose()


@respx.mock
async def test_source_down_degrades_not_crashes(monkeypatch):
    async def _no_sleep(_):  # skip real backoff waits
        return None

    monkeypatch.setattr("core.connectors.base.asyncio.sleep", _no_sleep)
    respx.get(url__regex=r"esearch\.fcgi").mock(return_value=httpx.Response(500))
    conn = PubMedConnector()
    result = await conn.search("lung", {})
    assert result.available is False
    assert "error" in result.note
    await conn.aclose()


@respx.mock
async def test_pdq_guideline_search():
    refs = [{"title": "Lung PDQ", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    respx.get("https://www.cancer.gov/lung").mock(
        return_value=httpx.Response(200, text="<html><body>Treatment options for disease.</body></html>")
    )
    conn = PdqConnector(refs=refs)
    result = await conn.search("lung treatment", {})
    assert result.citations
    assert "Treatment options" in result.citations[0].snippet
    await conn.aclose()


def test_dedup_collapses_cross_source():
    a = Citation(id_type=IdType.PMID, id="111", source="pubmed", snippet="")
    b = Citation(id_type=IdType.PMID, id="111", source="pubmed", snippet="longer abstract")
    out = dedup([a, b])
    assert len(out) == 1
    assert out[0].snippet == "longer abstract"


# ---------------------------------------------------------------------------
# NEW TESTS
# ---------------------------------------------------------------------------


@respx.mock
async def test_clinicaltrials_rejects_unknown_nct():
    """404 from ClinicalTrials for a bad NCT id → verify() returns False (no retry)."""
    respx.get(url__regex=r"/studies/NCT").mock(return_value=httpx.Response(404))
    conn = ClinicalTrialsConnector()
    result = await conn.verify(
        Citation(id_type=IdType.NCT, id="NCT99999999", source="clinicaltrials")
    )
    assert result is False
    await conn.aclose()


@respx.mock
async def test_openfda_verify_false_for_unknown_set_id():
    """openFDA returns empty results for an unknown set_id → verify() is False."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    conn = OpenFdaConnector()
    result = await conn.verify(
        Citation(id_type=IdType.URL, id="unknown-set-id-xyz", source="openfda")
    )
    assert result is False
    await conn.aclose()


@respx.mock
async def test_openfda_verify_true_for_known_set_id():
    """openFDA returns a result for a known set_id → verify() is True."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=OPENFDA_LABEL)
    )
    conn = OpenFdaConnector()
    result = await conn.verify(
        Citation(id_type=IdType.URL, id="abc-set-id", source="openfda")
    )
    assert result is True
    await conn.aclose()


@respx.mock
async def test_clinicaltrials_source_down_degrades_not_crashes(monkeypatch):
    """HTTP 500 from ClinicalTrials search → available=False, no exception."""
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("core.connectors.base.asyncio.sleep", _no_sleep)
    respx.get(url__regex=r"/studies\?").mock(return_value=httpx.Response(500))
    conn = ClinicalTrialsConnector()
    result = await conn.search("osimertinib", {})
    assert result.available is False
    await conn.aclose()


@respx.mock
async def test_pdq_source_down_degrades_not_crashes(monkeypatch):
    """HTTP 500 fetching a PDQ summary → available=False, no exception."""
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("core.connectors.base.asyncio.sleep", _no_sleep)
    refs = [{"title": "Lung PDQ", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    respx.get("https://www.cancer.gov/lung").mock(return_value=httpx.Response(500))
    conn = PdqConnector(refs=refs)
    result = await conn.search("lung treatment", {})
    assert result.available is False
    await conn.aclose()


def test_dedup_prefers_entry_with_title():
    """dedup keeps the citation WITH a title when both share the same key."""
    no_title = Citation(id_type=IdType.PMID, id="333", source="pubmed", title="", snippet="")
    with_title = Citation(id_type=IdType.PMID, id="333", source="pubmed", title="A good title", snippet="")
    out = dedup([no_title, with_title])
    assert len(out) == 1
    assert out[0].title == "A good title"


# ---------------------------------------------------------------------------
# Phase-5 hardening tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_pdq_ssrf_disallowed_host_search_degrades():
    """A ref URL on a disallowed host is skipped → search returns unavailable."""
    # The connector is constructed with a ref pointing to an attacker-controlled host.
    refs = [
        {"title": "Evil ref", "url": "https://evil.example.com/steal", "match": ["lung"]}
    ]
    # No HTTP mock registered — the guard must block the fetch entirely.
    conn = PdqConnector(refs=refs)
    result = await conn.search("lung treatment", {})
    # No citations should come back; the result should be "unavailable".
    assert result.available is False
    await conn.aclose()


@respx.mock
async def test_pdq_ssrf_non_https_scheme_search_degrades():
    """A ref URL using http:// scheme is blocked → search returns unavailable."""
    refs = [
        {"title": "Insecure ref", "url": "http://www.cancer.gov/lung", "match": ["lung"]}
    ]
    conn = PdqConnector(refs=refs)
    result = await conn.search("lung treatment", {})
    assert result.available is False
    await conn.aclose()


@respx.mock
async def test_pdq_ssrf_disallowed_host_verify_returns_false():
    """verify() for a citation whose URL points to a disallowed host → False, no fetch."""
    refs = [{"title": "OK ref", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    conn = PdqConnector(refs=refs)
    # Citation URL is on a disallowed host — not in _CONSTANT_ALLOWED_HOSTS or refs.
    citation = Citation(
        id_type=IdType.URL,
        id="https://169.254.169.254/latest/meta-data/",
        source="pdq",
        snippet="",
    )
    result = await conn.verify(citation)
    assert result is False
    await conn.aclose()


@respx.mock
async def test_pdq_ssrf_http_scheme_verify_returns_false():
    """verify() for an http:// URL → False (non-https scheme blocked)."""
    refs = [{"title": "OK ref", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    conn = PdqConnector(refs=refs)
    citation = Citation(
        id_type=IdType.URL,
        id="http://www.cancer.gov/lung",
        source="pdq",
        snippet="",
    )
    result = await conn.verify(citation)
    assert result is False
    await conn.aclose()


@respx.mock
async def test_pdq_verify_snippet_present_returns_true():
    """verify(): 200 response containing the citation's snippet → True."""
    refs = [{"title": "Lung PDQ", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    respx.get("https://www.cancer.gov/lung").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Treatment options for lung cancer patients include surgery.</body></html>",
        )
    )
    conn = PdqConnector(refs=refs)
    citation = Citation(
        id_type=IdType.URL,
        id="https://www.cancer.gov/lung",
        source="pdq",
        snippet="Treatment options for lung cancer patients include surgery.",
    )
    result = await conn.verify(citation)
    assert result is True
    await conn.aclose()


@respx.mock
async def test_pdq_verify_snippet_absent_returns_false():
    """verify(): 200 response that does NOT contain the snippet → False."""
    refs = [{"title": "Lung PDQ", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    respx.get("https://www.cancer.gov/lung").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Completely unrelated page content here.</body></html>",
        )
    )
    conn = PdqConnector(refs=refs)
    citation = Citation(
        id_type=IdType.URL,
        id="https://www.cancer.gov/lung",
        source="pdq",
        snippet="Treatment options for lung cancer patients include surgery.",
    )
    result = await conn.verify(citation)
    assert result is False
    await conn.aclose()


@respx.mock
async def test_pdq_verify_no_snippet_falls_back_to_liveness():
    """verify(): no snippet → falls back to 200-liveness check → True."""
    refs = [{"title": "Lung PDQ", "url": "https://www.cancer.gov/lung", "match": ["lung"]}]
    respx.get("https://www.cancer.gov/lung").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Any content at all.</body></html>",
        )
    )
    conn = PdqConnector(refs=refs)
    citation = Citation(
        id_type=IdType.URL,
        id="https://www.cancer.gov/lung",
        source="pdq",
        snippet="",  # empty — no content-identity check
    )
    result = await conn.verify(citation)
    assert result is True
    await conn.aclose()


@respx.mock
async def test_openfda_verify_consistent_drug_name_returns_true():
    """verify(): set_id exists and brand name matches citation title → True."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=OPENFDA_LABEL)
    )
    conn = OpenFdaConnector()
    citation = Citation(
        id_type=IdType.URL,
        id="abc-set-id",
        source="openfda",
        title="Tagrisso / osimertinib drug label",
    )
    result = await conn.verify(citation)
    assert result is True
    await conn.aclose()


@respx.mock
async def test_openfda_verify_mismatched_drug_name_returns_false():
    """verify(): set_id exists but drug name is inconsistent with citation title → False."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=OPENFDA_LABEL)
    )
    conn = OpenFdaConnector()
    # The label has Tagrisso/osimertinib but the citation claims a totally different drug.
    citation = Citation(
        id_type=IdType.URL,
        id="abc-set-id",
        source="openfda",
        title="Keytruda / pembrolizumab drug label",
    )
    result = await conn.verify(citation)
    assert result is False
    await conn.aclose()


@respx.mock
async def test_openfda_verify_no_title_falls_back_to_existence():
    """verify(): citation has no title → falls back to existence check → True."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=OPENFDA_LABEL)
    )
    conn = OpenFdaConnector()
    citation = Citation(
        id_type=IdType.URL,
        id="abc-set-id",
        source="openfda",
        title="",  # no title — skip content-identity, use existence
    )
    result = await conn.verify(citation)
    assert result is True
    await conn.aclose()


# ---------------------------------------------------------------------------
# Bug-fix tests: ClinicalTrials.gov sanitization + openFDA relevance filter
# ---------------------------------------------------------------------------

_OPENFDA_MIXED_LABELS = {
    "results": [
        {
            # Relevant: mentions NSCLC
            "set_id": "relevant-set-id",
            "openfda": {"brand_name": ["Tagrisso"], "generic_name": ["osimertinib"]},
            "indications_and_usage": [
                "Tagrisso is indicated for non-small cell lung cancer patients with EGFR mutations."
            ],
        },
        {
            # Junk: only mentions rheumatoid arthritis — no lung cancer relevance
            "set_id": "junk-set-id",
            "openfda": {"brand_name": ["Naproxen"], "generic_name": ["naproxen"]},
            "indications_and_usage": [
                "Naproxen is indicated for rheumatoid arthritis, osteoarthritis, and pain relief."
            ],
        },
    ]
}

_OPENFDA_ALL_JUNK = {
    "results": [
        {
            "set_id": "junk-only-set-id",
            "openfda": {"brand_name": ["Naproxen"], "generic_name": ["naproxen"]},
            "indications_and_usage": [
                "Naproxen is indicated for rheumatoid arthritis and pain."
            ],
        },
    ]
}


@respx.mock
async def test_clinicaltrials_sanitizes_malformed_query():
    """A query with %, :, parens, quotes, and >6 words is sanitized before sending.

    The URL used for the actual GET must not contain raw % (from the original
    term), colons, or parentheses derived from the query string, and the
    query.term/query.cond params must be length-capped.
    """
    import urllib.parse

    captured_urls: list[str] = []

    def _capture_and_ok(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json=CTGOV_STUDIES)

    respx.get(url__regex=r"/studies\?").mock(side_effect=_capture_and_ok)

    nasty_query = (
        "non-small cell lung adenocarcinoma EGFR exon 19 deletion "
        "PD-L1 TPS 10%: (biomarker \"positive\") stage:IV"
    )
    conn = ClinicalTrialsConnector()
    result = await conn.search(nasty_query, {})

    assert result.available, f"Search should succeed, got: {result.note}"
    assert len(captured_urls) == 1, "Connector should make exactly one request"

    url = captured_urls[0]
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)

    # The sanitized value must not contain the problematic chars. (query.term only — see
    # ClinicalTrialsConnector: query.cond was dropped to avoid CT.gov's AND over-restriction.)
    for param in ("query.term",):
        val = qs.get(param, [""])[0]
        assert "%" not in val, f"{param} still contains '%': {val!r}"
        assert ":" not in val, f"{param} still contains ':': {val!r}"
        assert "(" not in val and ")" not in val, f"{param} still contains parens: {val!r}"
        # Length cap: no more than _MAX_CHARS characters.
        assert len(val) <= 120, f"{param} value too long: {len(val)} chars"

    # Result should parse correctly (no 400 path taken).
    assert result.citations[0].id == "NCT01234567"
    await conn.aclose()


@respx.mock
async def test_openfda_filters_junk_keeps_relevant():
    """Mixed results: junk label (arthritis) filtered out, NSCLC label kept."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=_OPENFDA_MIXED_LABELS)
    )
    query = "non-small cell lung cancer EGFR osimertinib"
    conn = OpenFdaConnector()
    result = await conn.search(query, {})

    assert result.available
    ids = {c.id for c in result.citations}
    assert "relevant-set-id" in ids, "Relevant NSCLC label should be kept"
    assert "junk-set-id" not in ids, "Junk arthritis label should be filtered out"
    await conn.aclose()


@respx.mock
async def test_openfda_all_junk_returns_empty_available():
    """All returned labels are irrelevant → empty citations, available=True."""
    respx.get(url__regex=r"api\.fda\.gov").mock(
        return_value=httpx.Response(200, json=_OPENFDA_ALL_JUNK)
    )
    query = "non-small cell lung cancer EGFR osimertinib"
    conn = OpenFdaConnector()
    result = await conn.search(query, {})

    assert result.available, "available must be True even when all labels are filtered"
    assert result.citations == [], f"Expected empty citations, got: {result.citations}"
    await conn.aclose()
