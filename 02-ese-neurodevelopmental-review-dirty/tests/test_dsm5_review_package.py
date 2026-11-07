"""Contract tests for the DSM-5 neurodevelopmental review package."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.sr.config import SRConfig
from core.sr.protocol.validate import validate_vertical


ROOT = Path(__file__).resolve().parents[1]
VERTICAL = ROOT / "verticals" / "dsm5-neurodevelopmental-criteria-mechanisms"
INPUTS = ROOT / "review_inputs" / "dsm5-neurodevelopmental-criteria-mechanisms"


def _load(name: str):
    return yaml.safe_load((INPUTS / name).read_text(encoding="utf-8"))


def _criteria():
    crosswalk = _load("criterion_crosswalk.yaml")
    return [
        criterion
        for diagnosis in crosswalk["diagnoses"].values()
        for criterion in diagnosis["criteria"]
    ]


def test_vertical_loads_and_has_no_structural_errors():
    cfg = SRConfig(VERTICAL)
    assert cfg.short_id == "dsm5-nd-criteria-mechanisms"
    assert cfg.review_title.startswith("DSM-5 Neurodevelopmental Criteria")
    assert len(cfg.extraction_fields()) >= 50
    report = validate_vertical(cfg.vertical, root=VERTICAL)
    assert report.ok, report.errors


def test_every_target_criterion_has_unique_controlled_id_and_paraphrase():
    criteria = _criteria()
    ids = [c["id"] for c in criteria]
    assert len(criteria) == 47
    assert len(ids) == len(set(ids))
    assert {c["type"] for c in criteria} == {"symptom", "gate"}
    assert all(c["label"].strip() for c in criteria)
    assert all("DSM-5" not in c["label"] for c in criteria)


def test_crosswalk_covers_four_diagnoses_and_three_sld_domains():
    crosswalk = _load("criterion_crosswalk.yaml")
    assert set(crosswalk["diagnoses"]) == {"ADHD", "ASD", "SLD", "DCD"}
    sld_domains = {
        c["domain"] for c in crosswalk["diagnoses"]["SLD"]["criteria"]
        if c["type"] == "symptom"
    }
    assert {"reading", "written_expression", "mathematics"} <= sld_domains


def test_shared_domains_and_mechanisms_have_required_taxonomy():
    matrix = _load("domain_matrix.yaml")
    assert len(matrix["domains"]) == 8
    assert {"ADHD", "ASD", "SLD", "DCD"} <= set(matrix["domains"][0]["diagnoses"])
    assert set(matrix["mechanism_levels"]) == {
        "direct_human", "indirect_human", "preclinical", "theoretical"
    }
    assert len(matrix["intervention_categories"]) == 5
    assert set(matrix["intervention_statuses"]) == {
        "established", "experimental", "off_label", "unclear"
    }


def test_extraction_keeps_symptom_function_and_harm_outcomes_separate():
    schema = yaml.safe_load(
        (VERTICAL / "extraction_schema.yaml").read_text(encoding="utf-8")
    )
    fields = {
        item["name"]
        for group in schema["fields"].values()
        for item in group
    }
    assert {
        "symptom_change_estimate",
        "function_change_estimate",
        "participation_change_estimate",
        "quality_of_life_change_estimate",
        "harms_estimate",
        "durability_timepoint",
    } <= fields
    assert schema["rules"][-1].startswith("Reviewer-1 output remains draft")
    narrative_fields = {
        item["name"]
        for group in schema["fields"].values()
        for item in group
        if item.get("pool") == "narrative"
    }
    assert {
        "construct_validity_estimate",
        "diagnostic_discrimination_estimate",
        "functional_association_estimate",
        "mechanism_effect_estimate",
        "symptom_change_estimate",
        "function_change_estimate",
        "harms_estimate",
    } <= narrative_fields


def test_living_review_preserves_gates_and_no_silent_overwrite():
    living = _load("living_review.yaml")
    assert living["model"] == "staged_snapshot_then_living"
    assert living["updates"]["cadence"] == "quarterly"
    assert living["updates"]["no_silent_overwrite"] is True
    assert living["gates"]["ai_reviewer_2"] == "required_for_ai_cross_check"
    assert living["gates"]["human_reviewer_2"] == "required_before_reconciliation"
    assert living["gates"]["reconciliation"] == "required_after_human_reviewer_2"
    assert living["gates"]["clinical_signoff"] == "required_before_public_clinical_package"


def test_evidence_record_schema_requires_provenance_and_separate_gates():
    schema = _load("evidence_record_schema.yaml")
    assert {
        "record_id",
        "source_provenance",
        "evidence_stream",
        "criterion_ids",
        "ai_reviewer_1",
        "ai_reviewer_2",
        "human_reviewer_2",
        "reconciliation",
        "clinical_signoff",
    } <= set(schema["required"])
    assert set(schema["properties"]["evidence_stream"]["values"]) == {
        "criterion_validation", "overlap", "mechanism", "intervention"
    }
    assert set(schema["properties"]["outcomes"]["separate_keys"]) >= {
        "symptom", "function", "participation", "harms", "durability"
    }


def test_clinician_template_keeps_draft_boundary():
    template = (INPUTS / "clinician_package_template.md").read_text(encoding="utf-8")
    assert "DRAFT" in template
    assert "Reviewer 2" in template
    assert "clinical sign-off" in template
    assert "A lack of evidence is not evidence against a criterion" in template


def test_dcd_crosswalk_tracks_impairment_on_b_and_exclusion_on_d():
    crosswalk = _load("criterion_crosswalk.yaml")
    dcd = {c["id"]: c for c in crosswalk["diagnoses"]["DCD"]["criteria"]}
    assert set(dcd) == {"DCD-A1", "DCD-B", "DCD-C", "DCD-D"}
    assert dcd["DCD-B"]["domain"] == "impairment"
    assert dcd["DCD-D"]["domain"] == "exclusion"


def test_dual_ai_then_human_gate_is_fail_closed():
    from core.sr.review_gates import validate_review_gates

    incomplete = validate_review_gates({})
    assert not incomplete.ok
    assert any("human_reviewer_2" in error for error in incomplete.errors)

    complete = validate_review_gates({
        "ai_reviewer_1": {"provider": "codex", "status": "complete", "timestamp": "2026-07-30T12:00:00Z"},
        "ai_reviewer_2": {"provider": "claude", "status": "complete", "timestamp": "2026-07-30T12:05:00Z"},
        "human_reviewer_2": {"reviewer_name": "Named clinical expert", "decision": "approved", "timestamp": "2026-07-30T13:00:00Z"},
        "reconciliation": {"adjudicator": "Named clinical expert", "decision": "complete", "timestamp": "2026-07-30T13:10:00Z"},
        "clinical_signoff": {"status": "approved"},
    })
    assert complete.ok, complete.errors

    ai_as_human = validate_review_gates({
        "ai_reviewer_1": {"provider": "codex", "status": "complete", "timestamp": "x"},
        "ai_reviewer_2": {"provider": "claude", "status": "complete", "timestamp": "y"},
        "human_reviewer_2": {"reviewer_name": "Claude", "decision": "approved", "timestamp": "z"},
        "reconciliation": {"adjudicator": "Claude", "decision": "complete", "timestamp": "z"},
        "clinical_signoff": {"status": "approved"},
    })
    assert not ai_as_human.ok
    assert any("AI provider identity" in error for error in ai_as_human.errors)


def test_dsm5_grade_is_stratified_and_fail_closed(monkeypatch):
    from core.sr.grade import grade_outcomes
    from core.sr.models import ExtractedStudy, ExtractedValue, RoBAssessment, ScreenDecision, ScreeningResult

    monkeypatch.setenv("SR_VERTICAL", "dsm5-neurodevelopmental-criteria-mechanisms")
    extractions = [ExtractedStudy(
        pmid="1", evidence_stream="criterion_validation", diagnosis="ADHD",
        criterion_ids=["ADHD-I1"], age_stratum="preschool_childhood",
        values=[ExtractedValue(field="construct_validity_estimate", group="validation", value="0.80")],
    )]
    screening = [ScreeningResult(
        pmid="1", decision=ScreenDecision.INCLUDE, reason="mapped criterion",
        confidence=0.9, study_design="systematic_review", evidence_stream="criterion_validation",
    )]
    # AMSTAR-2 High is favorable and must not be counted as high risk; missing
    # full-text verification still forces the conservative ceiling.
    rows = grade_outcomes(extractions, screening, [RoBAssessment(
        pmid="1", tool="amstar2", overall="High",
    )])
    row = next(r for r in rows if r.field == "construct_validity_estimate")
    assert "criterion_ids=ADHD-I1" in row.stratum
    assert row.certainty == "Very Low"
    assert not any("Risk of bias (-1)" in d for d in row.downgrades)


def test_claim_status_rules_reserve_rejected_for_direct_falsification():
    schema = _load("evidence_record_schema.yaml")
    rejected = schema["claim_status_rules"]["rejected"]
    assert "explicit_falsification_rule" in rejected["requires"]
    assert "direct_disconfirming_evidence" in rejected["requires"]
    assert "human_reviewer_2" in rejected["requires"]
    assert "clinical_signoff" in rejected["requires"]


def test_evidence_stream_axis_is_requested_and_serializable():
    from core.models import Citation
    from core.sr.screen import build_screening_prompt

    cfg = SRConfig(VERTICAL)
    _system, prompt = build_screening_prompt([
        Citation(id_type="PMID", id="1", source="test", title="Mapped criterion study", snippet="abstract")
    ], cfg)
    assert "evidence stream" in prompt
    assert "criterion_validation / overlap / mechanism / intervention" in prompt


def test_included_records_cannot_bypass_stream_validation():
    from core.sr.models import ScreenDecision, ScreeningResult
    from core.sr.screen import subgroup_coverage_errors

    cfg = SRConfig(VERTICAL)
    errors = subgroup_coverage_errors([
        ScreeningResult(pmid="1", decision=ScreenDecision.INCLUDE, reason="x", confidence=0.9)
    ], cfg)
    assert errors and "no 'evidence_stream' label" in errors[0]
