import json

import pytest

from screen_prove.cli import main
from screen_prove.contracts import IntegrityError, canonical_bytes
from screen_prove.dedup import deduplicate
import screen_prove.validation as validation
from screen_prove.validation import (
    ALL_VALIDATION_CASES,
    CATEGORIES,
    COMPONENTS,
    EXCLUDED_COVERAGE,
    REQUIRED_COVERAGE,
    VALIDATION_MATRIX_VERSION,
    build_validation_matrix,
    build_validation_receipt,
    missing_required_coverage,
)


# --- the matrix itself -------------------------------------------------------


def test_every_validation_case_passes_on_the_real_modules():
    results = build_validation_matrix()
    failures = [result.to_dict() for result in results if not result.passed]
    assert failures == []


def test_validation_matrix_covers_every_required_component():
    results = build_validation_matrix()
    assert {result.component for result in results} == set(COMPONENTS)


def test_validation_matrix_covers_every_required_category():
    results = build_validation_matrix()
    assert {result.category for result in results} == set(CATEGORIES)


def test_required_coverage_matrix_is_the_full_grid_minus_documented_exclusions():
    # Every exclusion must carry a non-empty, documented reason -- an
    # excluded (component, category) can never be a silent gap.
    for component, exclusions in EXCLUDED_COVERAGE.items():
        assert component in COMPONENTS
        for category, reason in exclusions.items():
            assert category in CATEGORIES
            assert isinstance(reason, str) and reason.strip()
    for component in COMPONENTS:
        expected = set(CATEGORIES) - set(EXCLUDED_COVERAGE.get(component, {}))
        assert REQUIRED_COVERAGE[component] == expected


def test_validation_matrix_covers_every_required_component_category_combination():
    # This is the explicit per-combination assertion the union checks above
    # cannot provide on their own: every (component, category) pair that
    # REQUIRED_COVERAGE demands must have its own registered, executed case,
    # not merely appear somewhere in the separate component/category unions.
    results = build_validation_matrix()
    present = {(result.component, result.category) for result in results}
    missing = [
        (component, category)
        for component in COMPONENTS
        for category in REQUIRED_COVERAGE[component]
        if (component, category) not in present
    ]
    assert missing == []


def test_missing_required_coverage_reports_every_required_gap_when_no_cases_ran():
    missing = missing_required_coverage([])
    expected = sorted(
        (
            {"component": component, "category": category}
            for component in COMPONENTS
            for category in REQUIRED_COVERAGE[component]
        ),
        key=lambda entry: (entry["component"], entry["category"]),
    )
    assert sorted(missing, key=lambda entry: (entry["component"], entry["category"])) == expected
    assert len(missing) > 0


def test_missing_required_coverage_is_empty_for_the_real_matrix():
    results = build_validation_matrix()
    assert missing_required_coverage(results) == []


def test_validation_case_ids_are_unique_and_match_the_registered_cases():
    results = build_validation_matrix()
    ids = [result.case_id for result in results]
    assert len(ids) == len(set(ids))
    assert len(ids) == len(ALL_VALIDATION_CASES)


def test_build_validation_receipt_is_byte_stable():
    receipt_a = build_validation_receipt()
    receipt_b = build_validation_receipt()
    assert canonical_bytes(receipt_a) == canonical_bytes(receipt_b)


def test_build_validation_receipt_passes_and_reports_every_case():
    receipt = build_validation_receipt()
    assert receipt["version"] == VALIDATION_MATRIX_VERSION
    assert receipt["passed"] is True
    assert receipt["missing_coverage"] == []
    assert receipt["required_coverage"] == {
        component: sorted(REQUIRED_COVERAGE[component]) for component in COMPONENTS
    }
    assert len(receipt["cases"]) == len(ALL_VALIDATION_CASES)
    assert len(receipt["matrix_hash"]) == 64


def test_build_validation_receipt_fails_closed_when_a_required_combination_has_no_case(monkeypatch):
    # An incomplete registry (here, simulated by dropping the one registered
    # protocol/missing case) must never let `passed` report True just
    # because the cases that did run all happened to pass.
    def incomplete_matrix():
        return [
            result
            for result in [case() for case in ALL_VALIDATION_CASES]
            if not (result.component == "protocol" and result.category == "missing")
        ]

    monkeypatch.setattr(validation, "build_validation_matrix", incomplete_matrix)
    receipt = build_validation_receipt()
    assert receipt["passed"] is False
    assert {"component": "protocol", "category": "missing"} in receipt["missing_coverage"]


# --- spot checks on individual adversarial/negative cases --------------------


def test_case_protocol_malformed_input_hash_is_rejected():
    result = validation.case_protocol_malformed_input_hash()
    assert result.passed is True
    assert result.expected_outcome == result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_case_protocol_missing_required_field_is_rejected():
    result = validation.case_protocol_missing_required_field()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "missing"


def test_case_protocol_boundary_empty_structures_is_accepted():
    result = validation.case_protocol_boundary_empty_structures()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "boundary"


def test_case_dedup_ambiguous_title_year_collision_is_resolved_deterministically():
    result = validation.case_dedup_ambiguous_title_year_collision()
    assert result.passed is True
    assert result.category == "ambiguous"


def test_case_dedup_missing_identifying_fields_is_kept_not_dropped():
    result = validation.case_dedup_missing_identifying_fields()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "missing"


def test_case_dedup_malformed_non_dict_record_is_rejected():
    result = validation.case_dedup_malformed_non_dict_record()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_case_dedup_malformed_dict_missing_record_id_is_rejected():
    result = validation.case_dedup_malformed_dict_missing_record_id()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_deduplicate_rejects_a_dict_record_missing_record_id_directly():
    # Exercises the engine guard itself (not just the validation-matrix
    # case wrapper): a structurally valid dict record missing `record_id`
    # must be rejected deliberately, not silently kept.
    corpus = validation._freeze(validation._NORMAL_RIS)
    malformed_record = {key: value for key, value in corpus["records"][0].items() if key != "record_id"}
    tampered = dict(corpus)
    tampered["records"] = [malformed_record, corpus["records"][1]]
    tampered = validation._rehash_corpus(tampered)

    with pytest.raises(IntegrityError, match="record_id"):
        deduplicate(tampered)


def test_case_dedup_boundary_empty_record_list_is_accepted():
    result = validation.case_dedup_boundary_empty_record_list()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "boundary"


def test_case_dedup_tamper_corpus_hash_is_rejected():
    result = validation.case_dedup_tamper_corpus_hash()
    assert result.passed is True
    assert result.actual_outcome == "reject"


def test_case_screening_missing_decision_abstains_rather_than_inventing_a_choice():
    result = validation.case_screening_missing_decision_abstains()
    assert result.passed is True
    assert result.category == "missing"


def test_case_screening_malformed_invalid_adjudication_mode_is_rejected():
    result = validation.case_screening_malformed_invalid_adjudication_mode()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_case_screening_boundary_empty_record_set_is_accepted():
    result = validation.case_screening_boundary_empty_record_set()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "boundary"


def test_case_screening_tamper_cache_response_is_rejected():
    result = validation.case_screening_tamper_cache_response()
    assert result.passed is True
    assert result.actual_outcome == "reject"


def test_case_evaluation_normal_matched_labels_is_accepted():
    result = validation.case_evaluation_normal_matched_labels()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "normal"


def test_case_evaluation_missing_ai_result_is_counted():
    result = validation.case_evaluation_missing_ai_result_is_counted()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "missing"


def test_case_evaluation_malformed_invalid_label_value_is_rejected():
    result = validation.case_evaluation_malformed_invalid_label_value()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_case_evaluation_boundary_empty_labels_and_package_is_accepted():
    result = validation.case_evaluation_boundary_empty_labels_and_package()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "boundary"


def test_case_evaluation_tamper_labels_hash_is_rejected():
    result = validation.case_evaluation_tamper_labels_hash()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "tamper"


def test_case_extraction_missing_not_reported_field_is_accepted():
    result = validation.case_extraction_missing_not_reported_field()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "missing"


def test_case_extraction_ambiguous_estimated_without_note_is_rejected():
    result = validation.case_extraction_ambiguous_estimated_without_note_is_rejected()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "ambiguous"


def test_case_extraction_boundary_zero_length_span_is_rejected():
    result = validation.case_extraction_boundary_zero_length_span_is_rejected()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "boundary"


def test_case_rob_missing_domain_recorded_as_not_assessed():
    result = validation.case_rob_missing_domain_recorded_as_not_assessed()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "missing"


def test_case_rob_malformed_source_links_not_strings_is_rejected():
    result = validation.case_rob_malformed_source_links_not_strings()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_case_rob_boundary_invented_overall_when_all_not_assessed_is_rejected():
    result = validation.case_rob_boundary_invented_overall_when_all_not_assessed_is_rejected()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "boundary"


def test_case_synthesis_missing_count_field_is_rejected():
    result = validation.case_synthesis_missing_count_field()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "missing"


def test_case_synthesis_ambiguous_unsupported_measure_is_rejected():
    result = validation.case_synthesis_ambiguous_unsupported_measure_is_rejected()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "ambiguous"


def test_case_synthesis_malformed_events_exceed_total_is_rejected():
    result = validation.case_synthesis_malformed_events_exceed_total()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "malformed"


def test_case_replay_missing_record_coverage_is_rejected():
    result = validation.case_replay_missing_record_coverage_is_rejected()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "missing"


def test_case_replay_missing_cache_evidence_is_rejected():
    result = validation.case_replay_missing_cache_evidence_is_rejected()
    assert result.passed is True
    assert result.actual_outcome == "reject"
    assert result.category == "missing"


def test_case_replay_boundary_empty_result_set_is_accepted():
    result = validation.case_replay_boundary_empty_result_set()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "boundary"


def test_case_run_state_boundary_empty_queue_is_governed():
    result = validation.case_run_state_boundary_empty_queue_is_governed()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "boundary"


def test_case_run_state_normal_off_mode_ai_only_package_ignores_abstention():
    result = validation.case_run_state_normal_off_mode_ai_only_package_ignores_abstention()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "normal"


def test_case_synthesis_normal_odds_ratio_matches_golden_values():
    result = validation.case_synthesis_normal_odds_ratio()
    assert result.passed is True
    assert result.actual_outcome == "accept"
    assert result.category == "normal"


def test_case_rob_ambiguous_not_assessed_with_source_links_is_rejected():
    result = validation.case_rob_ambiguous_not_assessed_with_source_links()
    assert result.passed is True
    assert result.category == "ambiguous"


def test_case_synthesis_boundary_zero_event_cell_is_flagged_not_guessed():
    result = validation.case_synthesis_boundary_zero_event_cell()
    assert result.passed is True
    assert result.category == "boundary"


def test_case_replay_malformed_missing_manifest_is_rejected():
    result = validation.case_replay_malformed_missing_manifest()
    assert result.passed is True
    assert result.category == "malformed"


def test_case_run_state_missing_adjudication_blocks_export():
    result = validation.case_run_state_missing_adjudication_blocks_export()
    assert result.passed is True
    assert result.category == "missing"


def test_case_run_state_malformed_invalid_mode_is_rejected():
    result = validation.case_run_state_malformed_invalid_mode()
    assert result.passed is True
    assert result.category == "malformed"


# --- fail-closed regression coverage: a case must not silently report pass --


def test_case_protocol_tamper_lock_hash_fails_closed_if_verification_regresses(monkeypatch):
    def permissive_from_dict(cls, raw):
        return validation.ProtocolLock(raw["protocol"], raw["procedure"], raw["schema"], raw["input_hash"])

    monkeypatch.setattr(validation.ProtocolLock, "from_dict", classmethod(permissive_from_dict))
    result = validation.case_protocol_tamper_lock_hash()
    assert result.passed is False


def test_case_dedup_tamper_corpus_hash_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "deduplicate", lambda corpus: {**corpus, "records": corpus["records"]})
    result = validation.case_dedup_tamper_corpus_hash()
    assert result.passed is False


def test_case_extraction_tamper_value_mismatch_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "verify_extraction_record", lambda record: record)
    result = validation.case_extraction_tamper_value_mismatch()
    assert result.passed is False


def test_case_rob_tamper_domain_judgment_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "verify_rob_assessment", lambda assessment: assessment)
    result = validation.case_rob_tamper_domain_judgment()
    assert result.passed is False


def test_case_synthesis_tamper_point_estimate_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "verify_calculation", lambda calculation: calculation)
    result = validation.case_synthesis_tamper_point_estimate()
    assert result.passed is False


def test_case_replay_tamper_decision_changed_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "verify_replay", lambda *args, **kwargs: True)
    result = validation.case_replay_tamper_decision_changed()
    assert result.passed is False


def test_case_replay_missing_cache_evidence_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "verify_replay", lambda *args, **kwargs: True)
    result = validation.case_replay_missing_cache_evidence_is_rejected()
    assert result.passed is False


def test_case_evaluation_tamper_labels_hash_fails_closed_if_verification_regresses(monkeypatch):
    monkeypatch.setattr(validation, "verify_sealed_labels", lambda sealed: sealed)
    result = validation.case_evaluation_tamper_labels_hash()
    assert result.passed is False


# --- CLI integration ----------------------------------------------------------


def test_cli_validate_writes_report_and_exits_zero(tmp_path):
    report_path = tmp_path / "validation-report.json"
    exit_code = main(["validate", "--output", str(report_path)])
    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["version"] == VALIDATION_MATRIX_VERSION


def test_cli_validate_prints_report_without_an_output_path(capsys):
    exit_code = main(["validate"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
