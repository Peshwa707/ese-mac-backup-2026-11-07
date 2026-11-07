"""Offline tests for the systematic-review slice (Milestone 1).

All LLM and network calls are mocked; these verify the grounding/honesty
guarantees structurally: unverifiable PMIDs are dropped, dropped screening
records become UNCERTAIN (never lost), and missing extraction fields become NR
(never invented).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from core.connectors.pubmed import PubMedConnector
from core.models import Citation, IdType
from core.sr.config import load_sr_config
from core.sr.extract import _fill_missing_nr
from core.sr.models import (
    ExtractedStudy,
    ExtractedValue,
    ScreenDecision,
    ScreeningBatch,
    ScreeningResult,
)
from core.sr.screen import build_screening_prompt, screen_batch
from tests.conftest import PUBMED_EFETCH_XML, PUBMED_ESEARCH


# --- pure unit tests -------------------------------------------------------

def test_screening_prompt_includes_criteria_and_records():
    cfg = load_sr_config()
    recs = [Citation(id_type=IdType.PMID, id="111", source="pubmed", title="T", snippet="A phase 3 trial")]
    system, user = build_screening_prompt(recs, cfg)
    assert "Reviewer 1" in system
    assert "INCLUDE if ALL hold" in user
    assert "EXCLUDE if ANY holds" in user
    assert "111" in user  # the record is present
    assert "<records>" in user


def test_extraction_fills_every_missing_field_as_nr():
    cfg = load_sr_config()
    study = ExtractedStudy(
        pmid="111",
        values=[ExtractedValue(field="orr", group="efficacy", value="95%", quote="ORR was 95%")],
    )
    filled = _fill_missing_nr(study, cfg)
    names = {(v.group, v.field) for v in filled.values}
    expected = {(f["group"], f["name"]) for f in cfg.extraction_fields()}
    assert expected.issubset(names)  # nothing from the dictionary is omitted
    nr = [v for v in filled.values if v.field != "orr"]
    assert all(v.flag == "NR" and v.value == "NR" for v in nr)


def test_screen_batch_recovers_dropped_and_drops_hallucinated(monkeypatch):
    cfg = load_sr_config()
    recs = [
        Citation(id_type=IdType.PMID, id="111", source="pubmed", title="t1", snippet="s1"),
        Citation(id_type=IdType.PMID, id="222", source="pubmed", title="t2", snippet="s2"),
    ]

    def fake_parse(schema, system, user, **kw):
        # Returns a decision for 111, omits 222, invents 999.
        return ScreeningBatch(results=[
            ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE, reason="ok", confidence=0.9),
            ScreeningResult(pmid="999", decision=ScreenDecision.INCLUDE, reason="hallucinated", confidence=0.9),
        ])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = screen_batch(recs, cfg)
    by = {r.pmid: r for r in out}
    assert set(by) == {"111", "222"}              # 999 dropped, 222 recovered
    assert by["222"].decision == ScreenDecision.UNCERTAIN


def test_screen_batch_chunks_large_inputs(monkeypatch):
    cfg = load_sr_config()
    recs = [
        Citation(id_type=IdType.PMID, id=str(i), source="pubmed", title=f"t{i}", snippet=f"s{i}")
        for i in range(40)
    ]
    calls = []

    def fake_parse(schema, system, user, **kw):
        assert schema is ScreeningBatch
        calls.append(user)
        return ScreeningBatch(results=[])

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = screen_batch(recs, cfg)
    assert len(calls) == 4
    assert len(out) == 40
    assert all(r.decision == ScreenDecision.UNCERTAIN for r in out)


def test_screen_batch_falls_back_to_per_record_with_guardrails(monkeypatch):
    cfg = load_sr_config()
    recs = [
        Citation(id_type=IdType.PMID, id="111", source="pubmed", title="t1", snippet="s1"),
        Citation(id_type=IdType.PMID, id="222", source="pubmed", title="t2", snippet="s2"),
        Citation(id_type=IdType.PMID, id="333", source="pubmed", title="t3", snippet="s3"),
    ]
    batch_calls = 0
    per_record_calls = []

    def fake_parse(schema, system, user, **kw):
        nonlocal batch_calls
        if schema is ScreeningBatch:
            batch_calls += 1
            raise RuntimeError("no parseable structured output")
        assert schema is ScreeningResult
        per_record_calls.append(user)
        if '"pmid": "222"' in user:
            return ScreeningResult(
                pmid="999", decision=ScreenDecision.INCLUDE,
                reason="hallucinated", confidence=0.9)
        return ScreeningResult(
            pmid="111" if '"pmid": "111"' in user else "333",
            decision=ScreenDecision.INCLUDE, reason="eligible", confidence=0.9)

    monkeypatch.setattr("core.llm.client.parse", fake_parse)
    out = screen_batch(recs, cfg, batch_size=3)
    by = {r.pmid: r for r in out}
    assert batch_calls == 2
    assert len(per_record_calls) == 3
    assert set(by) == {"111", "222", "333"}
    assert by["111"].decision == ScreenDecision.INCLUDE
    assert by["222"].decision == ScreenDecision.UNCERTAIN
    assert by["333"].decision == ScreenDecision.INCLUDE


def test_audit_quote_anchoring_classifies_honestly():
    from core.sr.audit import audit_quote_anchoring

    abstracts = {"1": "The median PFS was 12·5 months and the ORR was 84%."}
    study = ExtractedStudy(pmid="1", values=[
        ExtractedValue(field="median_pfs", group="efficacy", value="12.5 months",
                       quote="median PFS was 12.5 months"),         # middle-dot in source
        ExtractedValue(field="orr", group="efficacy", value="84%", quote="the ORR was 84%"),
        ExtractedValue(field="year", group="identity", value="2024"),  # metadata, exempt
        ExtractedValue(field="median_os", group="efficacy", value="NR", flag="NR"),
        ExtractedValue(field="pfs_hr", group="efficacy", value="ESTIMATED: 0.42", flag="ESTIMATED"),
        ExtractedValue(field="cr_rate", group="efficacy", value="99%", quote="not in the abstract"),
    ])
    rep = audit_quote_anchoring(abstracts, [study])
    assert rep["substantive_values"] == 3          # 2 efficacy + 1 fabricated; year/NR/ESTIMATED excluded
    assert rep["anchored"] == 2                     # middle-dot value + orr
    assert rep["unanchored"] == 1                   # the 99% with no support
    assert rep["metadata_values"] == 1
    assert rep["estimated_for_human_review"] == 1
    assert rep["unanchored_items"][0]["field"] == "cr_rate"


@respx.mock
async def test_verify_bulk_resolves_present_and_rejects_absent():
    """Bulk verify must resolve real ids in one call and reject ids with no entry."""
    def _side_effect(request):
        qs = parse_qs(urlparse(str(request.url)).query)
        ids = [i for i in qs.get("id", [""])[0].split(",") if i]
        # '777' is present; '888' is returned only as an error entry (unresolved).
        result = {"uids": ids}
        for i in ids:
            result[i] = {"uid": i} if i != "888" else {"error": "cannot get document summary"}
        return httpx.Response(200, json={"result": result})

    respx.get(url__regex=r"esummary\.fcgi").mock(side_effect=_side_effect)
    conn = PubMedConnector()
    resolved = await conn.verify_bulk(["777", "888"])
    await conn.aclose()
    assert resolved == {"777"}  # 888 (error entry) is not counted as verified


# --- pipeline slice (mocked PubMed + LLM) ----------------------------------

def _esummary_side_effect(request):
    """Echo any requested id back as present, so verify() resolves it."""
    qs = parse_qs(urlparse(str(request.url)).query)
    ids = [i for i in qs.get("id", [""])[0].split(",") if i]
    result = {"uids": ids}
    for i in ids:
        result[i] = {"uid": i}
    return httpx.Response(200, json={"result": result})


@respx.mock
async def test_pipeline_slice_offline(monkeypatch):
    respx.get(url__regex=r"esearch\.fcgi").mock(return_value=httpx.Response(200, json=PUBMED_ESEARCH))
    respx.get(url__regex=r"efetch\.fcgi").mock(return_value=httpx.Response(200, text=PUBMED_EFETCH_XML))
    respx.get(url__regex=r"esummary\.fcgi").mock(side_effect=_esummary_side_effect)

    def fake_screen(batch, cfg):
        out = []
        for c in batch:
            label = (c.study_type or c.title or "").lower()
            if "case report" in label:
                out.append(ScreeningResult(pmid=c.id, decision=ScreenDecision.EXCLUDE,
                                           reason="case report/series excluded", confidence=0.95))
            else:
                out.append(ScreeningResult(pmid=c.id, decision=ScreenDecision.INCLUDE,
                                           reason="phase 3 RCT", confidence=0.9, modality="CAR-T"))
        return out

    def fake_extract(citation, cfg):
        return ExtractedStudy(pmid=citation.id, study_name="TESTTRIAL", modality="CAR-T",
                              values=[ExtractedValue(field="orr", group="efficacy", value="95%",
                                                     quote="ORR 95%")])

    monkeypatch.setattr("core.sr.pipeline.screen_batch", fake_screen)
    monkeypatch.setattr("core.sr.pipeline.extract_study", fake_extract)

    from core.sr.pipeline import run_sr
    run = await run_sr(retmax=10)

    assert run.counts.identified_pubmed == 2
    assert run.counts.after_dedup == 2
    assert run.counts.screened == 2            # both PMIDs verified live
    assert run.counts.id_unverified_removed == 0
    assert run.counts.included == 1
    assert run.counts.excluded_screening == 1
    assert run.exclusion_reasons.get("case report/series") == 1
    assert len(run.extractions) == 1
    assert run.extractions[0].study_name == "TESTTRIAL"
    # provenance was written and is readable via the store (path-independent).
    from core.render import provenance as provenance_store
    prov = provenance_store.read(run.run_id)
    assert prov is not None and prov.report_id == run.run_id
    # limitations are surfaced honestly
    assert any("ABSTRACT-LEVEL" in lim for lim in run.limitations)
