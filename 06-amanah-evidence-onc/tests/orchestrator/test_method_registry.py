"""WP-M4.3 — analysis-method registry with applicability preconditions."""
from core.sr.orchestrator import method_registry as mr


def test_unknown_method_fails_closed():
    ok, msgs = mr.select_method("magic", {"k": 10})
    assert ok is False and any("unknown method" in m for m in msgs)


def test_nma_on_disconnected_network_rejected():
    ok, msgs = mr.select_method("bayesian_nma",
                                {"effect_measure": "hr", "k": 6, "network_connected": False})
    assert ok is False and any("CONNECTED" in m for m in msgs)


def test_nma_on_connected_network_ok():
    ok, msgs = mr.select_method("frequentist_nma",
                                {"effect_measure": "hr", "k": 6, "network_connected": True,
                                 "transitivity_assessed": True})
    assert ok is True and msgs == []


def test_nma_warns_when_transitivity_not_assessed():
    ok, msgs = mr.select_method("frequentist_nma",
                                {"effect_measure": "hr", "k": 6, "network_connected": True})
    assert ok is True and any("transitivity" in m for m in msgs)   # soft warning, still ok


def test_method_not_applicable_to_effect_measure():
    ok, msgs = mr.select_method("frequentist_nma", {"effect_measure": "proportion", "k": 6,
                                                    "network_connected": True})
    assert ok is False and any("not applicable" in m for m in msgs)


def test_min_studies_enforced():
    ok, msgs = mr.select_method("random_effects", {"effect_measure": "hr", "k": 1})
    assert ok is False and any(">= 2" in m for m in msgs)


def test_random_effects_low_k_warns_but_ok():
    ok, msgs = mr.select_method("random_effects", {"effect_measure": "hr", "k": 2})
    assert ok is True and any("unstable" in m for m in msgs)


def test_fixed_effect_high_heterogeneity_warns():
    ok, msgs = mr.select_method("fixed_effect", {"effect_measure": "or", "k": 8, "i2": 80})
    assert ok is True and any("prefer random-effects" in m for m in msgs)


def test_missing_effect_measure_fails_closed():
    ok, msgs = mr.select_method("frequentist_nma",
                                {"k": 6, "network_connected": True, "transitivity_assessed": True})
    assert ok is False and any("requires an effect_measure" in m for m in msgs)


def test_missing_or_nonnumeric_k_fails_closed_without_error():
    ok, msgs = mr.select_method("random_effects", {"effect_measure": "hr"})        # k missing
    assert ok is False and any("study count" in m for m in msgs)
    ok2, msgs2 = mr.select_method("random_effects", {"effect_measure": "hr", "k": "two"})
    assert ok2 is False and any("study count" in m for m in msgs2)
    ok3, _ = mr.select_method("random_effects", {"effect_measure": "hr", "k": True})  # bool != count
    assert ok3 is False
    for bad_k in (float("nan"), float("inf"), -1, 2.5):                               # non-finite / non-count
        okx, _ = mr.select_method("random_effects", {"effect_measure": "hr", "k": bad_k})
        assert okx is False


def test_valid_random_effects_clean():
    ok, msgs = mr.select_method("random_effects", {"effect_measure": "hr", "k": 6, "i2": 30})
    assert ok is True and msgs == []
