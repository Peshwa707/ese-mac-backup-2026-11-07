import json
import pytest

from screen_prove.contracts import IntegrityError, object_hash, read_json, sha256_file
from screen_prove.dedup import deduplicate
from screen_prove.imports import freeze_import


RIS = """TY  - JOUR
TI  - Alpha trial
AB  - Alpha abstract.
C5  - 1001
DO  - 10.1000/ALPHA
PY  - 2020
ER  -
TY  - JOUR
TI  - Beta trial
AB  - Beta abstract.
C5  - 1002
ER  -
"""

NBIB = """PMID- 1001
TI  - Alpha trial
AB  - Alpha abstract.
DP  - 2020 Jan
AID - 10.1000/ALPHA [doi]

PMID- 1003
TI  - Gamma trial
DP  - 2021
"""


def test_freeze_import_preserves_source_hash_and_records(tmp_path):
    source = tmp_path / "review.ris"
    source.write_text(RIS, encoding="utf-8")
    corpus_path = tmp_path / "corpus.json"
    raw_dir = tmp_path / "raw"

    corpus = freeze_import(source, corpus_path, raw_dir, source_name="published-review")

    assert corpus["source_hash"] == sha256_file(source)
    assert len(corpus["records"]) == 2
    assert corpus["records"][0]["pmid"] == "1001"
    raw_files = list(raw_dir.glob("*-review.ris"))
    assert len(raw_files) == 1
    assert raw_files[0].read_text(encoding="utf-8") == RIS
    assert read_json(corpus_path)["corpus_hash"] == corpus["corpus_hash"]


def test_freeze_import_supports_nbib_and_stable_record_ids(tmp_path):
    source = tmp_path / "review.nbib"
    source.write_text(NBIB, encoding="utf-8")
    corpus = freeze_import(source, tmp_path / "corpus.json", tmp_path / "raw", source_name="pubmed")

    assert [record["pmid"] for record in corpus["records"]] == ["1001", "1003"]
    assert corpus["records"][0]["doi"] == "10.1000/ALPHA"
    assert len({record["record_id"] for record in corpus["records"]}) == 2


def test_freeze_import_normalizes_nbib_year_and_reads_lid_doi(tmp_path):
    source = tmp_path / "review.nbib"
    source.write_text("PMID- 1004\nTI  - Delta trial\nDP  - 2020 Jan 15\nLID - 10.1000/DELTA [doi]\n", encoding="utf-8")
    corpus = freeze_import(source, tmp_path / "corpus.json", tmp_path / "raw", source_name="pubmed")

    assert corpus["records"][0]["year"] == "2020"
    assert corpus["records"][0]["doi"] == "10.1000/DELTA"


def test_numeric_ris_id_is_not_invented_pmid(tmp_path):
    source = tmp_path / "review.ris"
    source.write_text("TY  - JOUR\nTI  - Epsilon trial\nID  - 7\nER  -\n", encoding="utf-8")
    corpus = freeze_import(source, tmp_path / "corpus.json", tmp_path / "raw", source_name="export")

    assert corpus["records"][0]["pmid"] == ""


def test_freezing_same_name_from_two_sources_does_not_overwrite_raw_bytes(tmp_path):
    first = tmp_path / "one" / "export.ris"
    second = tmp_path / "two" / "export.ris"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("TY  - JOUR\nTI  - First\nER  -\n", encoding="utf-8")
    second.write_text("TY  - JOUR\nTI  - Second\nER  -\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"

    freeze_import(first, tmp_path / "first.json", raw_dir, source_name="one")
    freeze_import(second, tmp_path / "second.json", raw_dir, source_name="two")

    assert sorted(path.read_text(encoding="utf-8") for path in raw_dir.glob("*-export.ris")) == [
        "TY  - JOUR\nTI  - First\nER  -\n",
        "TY  - JOUR\nTI  - Second\nER  -\n",
    ]


def test_dedup_prefers_first_record_and_records_reason():
    source = {
        "version": "frozen-corpus-v1",
        "source_name": "fixture",
        "source_file": "fixture.ris",
        "source_hash": "a" * 64,
        "records": [
            {"record_id": "r1", "title": "Same Study", "pmid": "1001", "year": "2020"},
            {"record_id": "r2", "title": "Same Study", "pmid": "1001", "year": "2020"},
            {"record_id": "r3", "title": "Other Study", "doi": "10.1000/other", "year": "2021"},
        ],
    }
    source["corpus_hash"] = object_hash({key: source[key] for key in ("version", "source_name", "source_file", "source_hash", "records")})

    result = deduplicate(source)

    assert [record["record_id"] for record in result["records"]] == ["r1", "r3"]
    assert result["counts"]["identified"] == 3
    assert result["counts"]["after_dedup"] == 2
    assert result["decisions"] == [{"record_id": "r2", "kept_record_id": "r1", "reason": "duplicate_pmid"}]


def test_dedup_rejects_tampered_corpus_hash():
    source = {
        "version": "frozen-corpus-v1",
        "source_name": "fixture",
        "source_file": "fixture.ris",
        "source_hash": "a" * 64,
        "records": [],
    }
    source["corpus_hash"] = object_hash(source)
    source["records"].append({"record_id": "r1", "title": "Tampered"})
    with pytest.raises(IntegrityError, match="corpus_hash"):
        deduplicate(source)
