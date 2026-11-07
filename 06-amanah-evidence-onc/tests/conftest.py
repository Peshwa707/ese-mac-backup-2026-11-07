"""Shared fixtures: canned source payloads and sample cases."""

from __future__ import annotations

import pytest

PUBMED_ESEARCH = {"esearchresult": {"idlist": ["111", "222"]}}

PUBMED_EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <ArticleTitle>Osimertinib in EGFR-mutated disease: a randomized phase 3 trial</ArticleTitle>
        <Abstract><AbstractText>Osimertinib improved progression-free survival versus comparator in this randomized phase 3 trial.</AbstractText></Abstract>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><History><PubMedPubDate><Year>2023</Year></PubMedPubDate></History></PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>222</PMID>
      <Article>
        <ArticleTitle>A case report of an unusual response</ArticleTitle>
        <Abstract><AbstractText>We describe a single patient with a durable response.</AbstractText></Abstract>
        <PublicationTypeList><PublicationType>Case Reports</PublicationType></PublicationTypeList>
        <PubDate><Year>2019</Year></PubDate>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""

PUBMED_ESUMMARY = {"result": {"uids": ["111"], "111": {"uid": "111", "title": "Osimertinib trial"}}}

CTGOV_STUDIES = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT01234567", "briefTitle": "Osimertinib study"},
                "descriptionModule": {"briefSummary": "A study of osimertinib in EGFR-mutated disease."},
                "designModule": {"phases": ["PHASE3"]},
                "statusModule": {"startDateStruct": {"date": "2022-01-01"}},
            }
        }
    ]
}

CTGOV_ONE = {
    "protocolSection": {"identificationModule": {"nctId": "NCT01234567"}}
}

OPENFDA_LABEL = {
    "results": [
        {
            "set_id": "abc-set-id",
            "openfda": {"brand_name": ["Tagrisso"], "generic_name": ["osimertinib"]},
            "indications_and_usage": ["Indicated for EGFR-mutated metastatic disease."],
        }
    ]
}


@pytest.fixture
def deid_case_clean() -> str:
    return (
        "A 62 year-old patient with lung adenocarcinoma, stage IV, EGFR exon 19 "
        "deletion, ECOG 1, progressed after first-line osimertinib."
    )


@pytest.fixture
def case_with_phi() -> str:
    return (
        "Mr. John Smith (MRN: 4456789, DOB 03/04/1962, john.smith@example.com) is a "
        "patient with lung adenocarcinoma."
    )
