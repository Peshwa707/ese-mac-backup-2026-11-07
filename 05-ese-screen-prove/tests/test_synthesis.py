import pytest

from screen_prove.contracts import IntegrityError, canonical_bytes, object_hash
from screen_prove.synthesis import (
    CALCULATION_VERSION,
    SUPPORTED_EFFECT_MEASURES,
    build_analysis_recipe,
    calculate_effect_measure,
    verify_calculation,
)


# --- build_analysis_recipe (protocol-supplied, bounded) ---------------------


def test_build_analysis_recipe_accepts_every_documented_measure():
    for measure in SUPPORTED_EFFECT_MEASURES:
        recipe = build_analysis_recipe("record:1", measure)
        assert recipe["measure"] == measure
        assert recipe["record_id"] == "record:1"


def test_build_analysis_recipe_rejects_an_unbounded_measure():
    with pytest.raises(IntegrityError, match="unsupported effect measure"):
        build_analysis_recipe("record:1", "hazard_ratio")


def test_build_analysis_recipe_rejects_an_empty_record_id():
    with pytest.raises(IntegrityError, match="record_id"):
        build_analysis_recipe("", "risk_ratio")


def test_build_analysis_recipe_is_byte_stable_for_identical_inputs():
    recipe_a = build_analysis_recipe("record:1", "risk_ratio")
    recipe_b = build_analysis_recipe("record:1", "risk_ratio")
    assert canonical_bytes(recipe_a) == canonical_bytes(recipe_b)


# --- calculate_effect_measure: golden numerical fixtures --------------------


def _counts(treated_events, treated_total, control_events, control_total):
    return {
        "treated_events": treated_events,
        "treated_total": treated_total,
        "control_events": control_events,
        "control_total": control_total,
    }


def test_calculate_risk_difference_matches_hand_verified_golden_values():
    # a=30/150 treated, b=15/150 control -> p1=0.2, p0=0.1, RD=0.1 exactly.
    # SE = sqrt(0.2*0.8/150 + 0.1*0.9/150) = sqrt(1/600) = 0.0408248290...
    # margin = 1.959963984540054 * SE = 0.0800151968...
    recipe = build_analysis_recipe("record:1", "risk_difference")
    result = calculate_effect_measure(recipe, _counts(30, 150, 15, 150))
    assert result["status"] == "calculated"
    assert result["point_estimate"] == pytest.approx(0.1, abs=1e-9)
    assert result["ci_low"] == pytest.approx(0.0199848, abs=1e-4)
    assert result["ci_high"] == pytest.approx(0.1800152, abs=1e-4)
    assert result["uncertainty_flags"] == []


def test_calculate_risk_ratio_point_estimate_is_an_exact_golden_value():
    # a=10/100 treated, b=5/100 control -> RR = (10/100)/(5/100) = 2.0 exactly.
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    result = calculate_effect_measure(recipe, _counts(10, 100, 5, 100))
    assert result["status"] == "calculated"
    assert result["point_estimate"] == pytest.approx(2.0, abs=1e-9)
    assert 0 < result["ci_low"] < result["point_estimate"] < result["ci_high"]


def test_calculate_odds_ratio_point_estimate_is_an_exact_golden_value():
    # a=20/100 treated, b=10/100 control -> OR = (20*90)/(10*80) = 2.25 exactly.
    recipe = build_analysis_recipe("record:1", "odds_ratio")
    result = calculate_effect_measure(recipe, _counts(20, 100, 10, 100))
    assert result["status"] == "calculated"
    assert result["point_estimate"] == pytest.approx(2.25, abs=1e-9)
    assert 0 < result["ci_low"] < result["point_estimate"] < result["ci_high"]


def test_calculate_effect_measure_is_byte_stable_for_identical_inputs():
    recipe = build_analysis_recipe("record:1", "risk_difference")
    result_a = calculate_effect_measure(recipe, _counts(30, 150, 15, 150))
    result_b = calculate_effect_measure(recipe, _counts(30, 150, 15, 150))
    assert canonical_bytes(result_a) == canonical_bytes(result_b)


# --- calculate_effect_measure: uncertainty flags, never inventing a value ---


def test_calculate_risk_ratio_flags_zero_event_cell_instead_of_guessing():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    result = calculate_effect_measure(recipe, _counts(0, 100, 5, 100))
    assert result["status"] == "insufficient_data"
    assert result["point_estimate"] is None
    assert result["ci_low"] is None
    assert result["ci_high"] is None
    assert "zero_event_cell_ratio_measure_undefined" in result["uncertainty_flags"]


def test_calculate_odds_ratio_flags_zero_nonevent_cell_instead_of_guessing():
    recipe = build_analysis_recipe("record:1", "odds_ratio")
    result = calculate_effect_measure(recipe, _counts(100, 100, 5, 100))
    assert result["status"] == "insufficient_data"
    assert result["point_estimate"] is None
    assert "zero_nonevent_cell_odds_ratio_undefined" in result["uncertainty_flags"]


def test_calculate_risk_difference_never_flags_a_zero_event_cell():
    # Risk difference is a linear measure: a zero-event or zero-nonevent cell
    # does not make it undefined, so it must be calculated, not flagged.
    recipe = build_analysis_recipe("record:1", "risk_difference")
    result = calculate_effect_measure(recipe, _counts(0, 100, 0, 100))
    assert result["status"] == "calculated"
    assert result["uncertainty_flags"] == []


# --- calculate_effect_measure: altered-input failure tests ------------------


def test_calculate_effect_measure_rejects_an_unsupported_recipe_version():
    recipe = dict(build_analysis_recipe("record:1", "risk_ratio"))
    recipe["version"] = "analysis-recipe-v0"
    with pytest.raises(IntegrityError, match="unsupported analysis recipe version"):
        calculate_effect_measure(recipe, _counts(10, 100, 5, 100))


def test_calculate_effect_measure_rejects_negative_counts():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    with pytest.raises(IntegrityError, match="must not be negative"):
        calculate_effect_measure(recipe, _counts(-1, 100, 5, 100))


def test_calculate_effect_measure_rejects_non_integer_counts():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    with pytest.raises(IntegrityError, match="must be an integer"):
        calculate_effect_measure(recipe, _counts(10.5, 100, 5, 100))


def test_calculate_effect_measure_rejects_boolean_counts():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    with pytest.raises(IntegrityError, match="must be an integer"):
        calculate_effect_measure(recipe, _counts(True, 100, 5, 100))


def test_calculate_effect_measure_rejects_events_exceeding_total():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    with pytest.raises(IntegrityError, match="must not exceed"):
        calculate_effect_measure(recipe, _counts(101, 100, 5, 100))


def test_calculate_effect_measure_rejects_a_zero_total():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    with pytest.raises(IntegrityError, match="greater than zero"):
        calculate_effect_measure(recipe, _counts(0, 0, 5, 100))


# --- verify_calculation: recomputes and detects tampering -------------------


def test_verify_calculation_accepts_a_valid_calculation():
    recipe = build_analysis_recipe("record:1", "risk_difference")
    result = calculate_effect_measure(recipe, _counts(30, 150, 15, 150))
    verified = verify_calculation(result)
    assert verified["record_id"] == "record:1"
    assert verified["point_estimate"] == pytest.approx(0.1, abs=1e-9)


def test_verify_calculation_accepts_a_valid_insufficient_data_result():
    recipe = build_analysis_recipe("record:1", "risk_ratio")
    result = calculate_effect_measure(recipe, _counts(0, 100, 5, 100))
    verified = verify_calculation(result)
    assert verified["status"] == "insufficient_data"


def test_verify_calculation_rejects_tampering_after_the_fact():
    recipe = build_analysis_recipe("record:1", "risk_difference")
    result = calculate_effect_measure(recipe, _counts(30, 150, 15, 150))
    result["point_estimate"] = 0.5
    with pytest.raises(IntegrityError, match="calculation_hash"):
        verify_calculation(result)


def test_verify_calculation_rejects_a_tampered_point_estimate_with_a_recomputed_hash():
    result = dict(calculate_effect_measure(build_analysis_recipe("record:1", "risk_difference"), _counts(30, 150, 15, 150)))
    result["point_estimate"] = 0.5
    body = {key: value for key, value in result.items() if key != "calculation_hash"}
    result["calculation_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="fresh recomputation"):
        verify_calculation(result)


def test_verify_calculation_rejects_tampered_inputs_with_a_recomputed_hash():
    result = dict(calculate_effect_measure(build_analysis_recipe("record:1", "risk_difference"), _counts(30, 150, 15, 150)))
    result["inputs"] = dict(result["inputs"])
    result["inputs"]["treated_events"] = 60
    body = {key: value for key, value in result.items() if key != "calculation_hash"}
    result["calculation_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="fresh recomputation"):
        verify_calculation(result)


def test_verify_calculation_rejects_an_unsupported_version_with_a_recomputed_hash():
    result = dict(calculate_effect_measure(build_analysis_recipe("record:1", "risk_difference"), _counts(30, 150, 15, 150)))
    result["version"] = "synthesis-calculation-v0"
    body = {key: value for key, value in result.items() if key != "calculation_hash"}
    result["calculation_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="unsupported synthesis calculation version"):
        verify_calculation(result)


def test_verify_calculation_rejects_an_unsupported_measure_with_a_recomputed_hash():
    result = dict(calculate_effect_measure(build_analysis_recipe("record:1", "risk_difference"), _counts(30, 150, 15, 150)))
    result["measure"] = "hazard_ratio"
    body = {key: value for key, value in result.items() if key != "calculation_hash"}
    result["calculation_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="unsupported effect measure"):
        verify_calculation(result)


def test_verify_calculation_rejects_a_non_mapping_inputs_field_with_a_recomputed_hash():
    result = dict(calculate_effect_measure(build_analysis_recipe("record:1", "risk_difference"), _counts(30, 150, 15, 150)))
    result["inputs"] = ["not", "a", "mapping"]
    body = {key: value for key, value in result.items() if key != "calculation_hash"}
    result["calculation_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="inputs"):
        verify_calculation(result)


def test_calculation_version_constant_matches_documented_body():
    result = calculate_effect_measure(build_analysis_recipe("record:1", "risk_ratio"), _counts(10, 100, 5, 100))
    assert result["version"] == CALCULATION_VERSION
