"""WP1 — lock RIS parsing of Embase/Ovid field codes (N2=abstract, C5=PMID).

Regression guard: Embase RIS exports carry the abstract in N2 (not AB) and the
PubMed ID in C5; the original parser read only AB/AN and silently dropped both.
"""
from core.connectors.imported import parse_ris

EMBASE_RIS = (
    "TY  - JOUR\n"
    "T1  - Teclistamab in Multiple Myeloma with One to Three Previous Lines\n"
    "N2  - Teclistamab significantly reduced the risk of progression versus SOC.\n"
    "C5  - 42212933\n"
    "DO  - 10.1056/NEJMoa2603870\n"
    "Y1  - 2026\n"
    "ER  - \n"
)

CLASSIC_RIS = (
    "TY  - JOUR\n"
    "T1  - Cilta-cel or Standard Care\n"
    "AB  - Ciltacabtagene autoleucel improved progression-free survival.\n"
    "AN  - 37272512\n"
    "ER  - \n"
)


def test_ris_embase_n2_abstract_and_c5_pmid():
    rec = parse_ris(EMBASE_RIS)[0]
    assert rec["abstract"].startswith("Teclistamab significantly reduced")
    assert rec["pmid"] == "42212933"
    assert rec["doi"] == "10.1056/NEJMoa2603870"
    assert rec["title"].startswith("Teclistamab in Multiple Myeloma")


def test_ris_classic_ab_an_still_work():
    rec = parse_ris(CLASSIC_RIS)[0]
    assert rec["abstract"] == "Ciltacabtagene autoleucel improved progression-free survival."
    assert rec["pmid"] == "37272512"


def test_ris_captures_m3_study_type():
    rec = parse_ris("TY  - JOUR\nT1  - X\nM3  - Conference Abstract\nC5  - 11111111\nER  - \n")[0]
    assert rec["study_type"] == "Conference Abstract"


def test_to_citation_propagates_study_type():
    from core.connectors.imported import to_citation
    c = to_citation({"title": "X", "pmid": "11111111", "study_type": "Conference Abstract"}, "Embase", "f.ris")
    assert c.study_type == "Conference Abstract"


def test_ris_multiple_records_split_on_ty():
    recs = parse_ris(EMBASE_RIS + CLASSIC_RIS)
    assert len(recs) == 2
    assert {r["pmid"] for r in recs} == {"42212933", "37272512"}
