"""Tests for trial-level deduplication (NCT/acronym grouping, conservative merging)."""

from __future__ import annotations

from core.connectors.pubmed import _parse_efetch
from core.sr.dedup_trials import (
    dedup_summary,
    group_by_trial,
    trial_acronym,
    trial_key,
)
from core.sr.models import ExtractedStudy, ExtractedValue


def _es(pmid, **fields):
    vals = [ExtractedValue(field=k, group="efficacy", value=v,
                           flag="NR" if v == "NR" else "") for k, v in fields.items()]
    return ExtractedStudy(pmid=pmid, values=vals)


def test_trial_acronym_detection():
    assert trial_acronym("Results of CARTITUDE-1 study") == "CARTITUDE-1"
    assert trial_acronym("the KarMMa-3 trial") == "KARMMA-3"
    assert trial_acronym("MagnetisMM-3 and LINKER-MM1") == "MAGNETISMM-3"
    assert trial_acronym("a novel BCMA CAR construct") is None
    assert trial_acronym("COVID-19 vaccination") is None        # stop-word, not a trial


def test_trial_key_prefers_nct_then_acronym():
    rec = {"title": "Teclistamab in MM (MajesTEC-1)", "abstract": "registered NCT03145181"}
    assert trial_key("1", rec, {"1": "NCT03145181"}) == ("NCT03145181", "nct")
    assert trial_key("1", rec, {}) == ("NCT03145181", "nct")          # regex fallback
    assert trial_key("2", {"title": "CARTITUDE-1 update", "abstract": ""}, {}) == ("CARTITUDE-1", "acronym")
    assert trial_key("3", {"title": "novel CAR", "abstract": ""}, {}) == (None, "singleton")


def test_three_reports_one_trial_collapse():
    ext = [_es("A", orr="84%", crs_any_grade="80%"), _es("B", orr="NR"), _es("C", orr="NR")]
    records = {"A": {"title": "Teclistamab results"}, "B": {"title": "Infections with teclistamab"},
               "C": {"title": "Population pharmacokinetics of teclistamab"}}
    nct = {"A": "NCT03145181", "B": "NCT03145181", "C": "NCT03145181"}
    groups = group_by_trial(ext, records, nct)
    assert len(groups) == 1                              # 3 reports -> 1 trial
    g = groups[0]
    assert g.anchor_pmid == "A" and set(g.member_pmids) == {"A", "B", "C"}
    assert dedup_summary(groups, 3) == {"reports": 3, "trials": 1, "by_key_source": {"nct": 1},
                                        "multi_report_trials": 1, "trials_with_conflicts": 0}


def test_distinct_ncts_not_merged():
    ext = [_es("D", orr="73%"), _es("E", orr="71%")]
    records = {"D": {"title": "KarMMa"}, "E": {"title": "KarMMa-3"}}
    groups = group_by_trial(ext, records, {"D": "NCT03361748", "E": "NCT03651128"})
    assert len(groups) == 2                              # different NCTs stay separate


def test_keyless_records_never_merged():
    ext = [_es("F", orr="50%"), _es("G", orr="60%")]
    records = {"F": {"title": "novel CAR construct"}, "G": {"title": "another CAR study"}}
    groups = group_by_trial(ext, records, {})
    assert len(groups) == 2 and all(g.key_source == "singleton" for g in groups)


def test_merge_fills_nr_and_flags_conflict():
    ext = [_es("A", orr="NR", cr="50%"), _es("B", orr="55%", cr="60%")]
    records = {"A": {"title": "Main results"}, "B": {"title": "Main results part 2"}}
    g = group_by_trial(ext, records, {"A": "NCT1", "B": "NCT1"})[0]
    vals = {v.field: v.value for v in g.extraction.values}
    assert vals["orr"] == "55%"                          # NR filled from sibling
    assert g.conflicts and "cr" in g.conflicts[0]        # cr 50 vs 60 flagged, not merged


def test_parse_efetch_extracts_nct():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID>
      <Article><ArticleTitle>A trial</ArticleTitle>
      <DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName>
      <AccessionNumberList><AccessionNumber>NCT03145181</AccessionNumber></AccessionNumberList>
      </DataBank></DataBankList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    c = _parse_efetch(xml)[0]
    assert c.extra_ids.get("NCT") == "NCT03145181"
