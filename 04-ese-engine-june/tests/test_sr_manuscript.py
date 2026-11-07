"""Tests for the manuscript export (model → MD/DOCX, assembly, chrome detection)."""

from __future__ import annotations

from pathlib import Path

from core.sr.config import load_sr_config
from core.sr.manuscript.assemble import build_components
from core.sr.manuscript.model import Component, Heading, Note, Paragraph, Table
from core.sr.manuscript.render_docx import component_to_docx
from core.sr.manuscript.render_md import component_to_md
from core.sr.models import (
    ExtractedStudy,
    ExtractedValue,
    PrismaCounts,
    ScreenDecision,
    ScreeningResult,
    SRRun,
)


def _synthetic_run():
    return SRRun(
        run_id="deadbeefcafebabe",
        review_title="Test Review: A vs B",
        pubmed_query="(myeloma) AND (CAR-T OR bispecific)",
        counts=PrismaCounts(identified_pubmed=100, after_dedup=100, id_unverified_removed=0,
                            screened=100, excluded_screening=80, included=1, uncertain=19),
        exclusion_reasons={"review/editorial/no-outcome": 50, "other / off-PICO": 30},
        screening=[ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE,
                                   reason="phase 3 RCT", confidence=0.9, modality="CAR-T")],
        extractions=[ExtractedStudy(pmid="111", study_name="TRIALX", modality="CAR-T",
                     values=[ExtractedValue(field="orr", group="efficacy", value="84%", quote="ORR 84%"),
                             ExtractedValue(field="median_os", group="efficacy", value="NR", flag="NR")])],
        named_trial_capture={"CARTITUDE-4": True},
        limitations=["AI reviewer model(s) produced the screening/extraction drafts; every "
                     "decision and value requires independent human verification and sign-off by "
                     "a reviewer-of-record."],
        provenance_id="deadbeefcafebabe",
        disclaimer="First pass; independent human verification required.",
    )


_RECORDS = {"111": {"pmid": "111", "title": "A trial of X", "year": 2024,
                    "abstract": "In this phase 3 trial the ORR was 84%."}}


def test_md_table_renders_pipes():
    c = Component("t", "T", [Table(headers=["A", "B"], rows=[["1", "2"]], caption="cap")])
    md = component_to_md(c)
    assert "| A | B |" in md and "| 1 | 2 |" in md and "*cap*" in md


def test_docx_roundtrip(tmp_path):
    from docx import Document
    c = Component("t", "My Component", [
        Heading(2, "Section"), Paragraph("hello world"),
        Table(headers=["Field", "Value"], rows=[["orr", "84%"]]),
        Note("complete me", kind="todo"),
    ])
    out = tmp_path / "c.docx"
    component_to_docx(c).save(str(out))
    assert out.exists() and out.stat().st_size > 0
    doc = Document(str(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "My Component" in text and "hello world" in text
    assert any(t.rows[1].cells[0].text == "orr" for t in doc.tables)


def test_assemble_produces_full_component_set():
    run, cfg = _synthetic_run(), load_sr_config()
    comps = build_components(run, cfg, _RECORDS, audit={"anchoring": {"anchored": 1,
            "substantive_values": 1, "anchored_rate": 1.0, "estimated_for_human_review": 0, "nr": 1},
            "resolution": {"resolved": 1, "checked": 1}})
    keys = [c.key for c in comps]
    assert keys == [
        "00_title_abstract", "01_introduction", "02_methods", "03_prisma_results",
        "04_included_abstracts", "05_study_characteristics", "06_risk_of_bias",
        "07_statistical_analysis", "08_grade", "09_discussion", "10_references", "11_supplementary",
        "13_ai_reporting",
    ]
    # Included abstract list contains the included study; stats stub is honest.
    inc = next(c for c in comps if c.key == "04_included_abstracts")
    assert any("A trial of X" in getattr(b, "text", "") for b in inc.blocks)
    stats = next(c for c in comps if c.key == "07_statistical_analysis")
    assert any(isinstance(b, Note) and "Phase B" in b.text for b in stats.blocks)


def test_assemble_tolerates_binary_and_continuous_stats_payloads():
    from core.sr.stats.meta import (
        BinPoolResult,
        ContPoolResult,
        MetaRegResult,
        MetaRegTerm,
        NumberNeeded,
    )
    from core.sr.stats.run import BinOutcome, ContOutcome, StatsResult

    run, cfg = _synthetic_run(), load_sr_config()
    stats = StatsResult(
        available=True,
        bin_outcomes=[BinOutcome(
            field="response_2x2",
            label="Response 2x2",
            results=[BinPoolResult(
                group="ALL", k=2, measure="RR", estimate=1.2, ci_lb=0.9, ci_ub=1.6,
                rd=0.08, rd_lb=0.02, rd_ub=0.14,
                number_needed=NumberNeeded("NNT", 12.5, 7.1, 50.0,
                                           "treatment event rate higher"),
            )],
            n_pooled=2,
            n_excluded=0,
        )],
        cont_outcomes=[ContOutcome(
            field="qol_score",
            label="Quality of life",
            results=[ContPoolResult(group="ALL", k=2, measure="SMD",
                                    estimate=0.35, ci_lb=-0.2, ci_ub=0.9)],
            n_pooled=2,
            n_excluded=0,
        )],
        meta_regression=MetaRegResult(
            True, False, "exploratory only", 6,
            [MetaRegTerm("age", -0.02, 0.01, -0.04, -0.01, 0.01)],
            QM=6.0, QM_p=0.01, QE=3.0, QE_p=0.2, I2_resid=12.0, R2=None,
        ),
    )
    comps = build_components(run, cfg, _RECORDS, stats=stats)
    assert next(c for c in comps if c.key == "07_statistical_analysis").blocks


def test_chrome_detection_finds_macos_bundle(monkeypatch):
    import core.render.render as render
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    bundle = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    monkeypatch.setattr(render.os, "access", lambda p, mode: p == bundle)
    assert render._find_chrome() == bundle
    assert render.pdf_available() is True
