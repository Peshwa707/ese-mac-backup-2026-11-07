"""WP-M4.2 — surrogate taxonomy + NMA indirectness + conclusion linter."""
from core.sr.orchestrator import evidence_strength as es


def test_classify_endpoint():
    assert es.classify_endpoint("Overall survival") == "definitive"
    assert es.classify_endpoint("OS") == "definitive"
    assert es.classify_endpoint("ORR") == "surrogate"
    assert es.classify_endpoint("progression-free survival") == "surrogate"
    assert es.classify_endpoint("widget count") == "unknown"


def test_surrogate_flag_carries_caveat():
    stats = {"outcomes": [{"field": "ORR", "results": [{"group": "ALL", "k": 10, "I2": 30.0}]}]}
    flag = es.assess(stats)["flags"][0]
    assert flag["endpoint_class"] == "surrogate" and "surrogate_caveat" in flag


def test_indirect_hr_gets_mandatory_indirectness_downgrade():
    stats = {"hr_outcomes": [{"field": "pfs_hr", "results": [{"group": "ALL", "k": 4, "I2": 20.0}]}]}
    direct = es.assess(stats, indirect=False)["flags"][0]
    indirect = es.assess(stats, indirect=True)["flags"][0]
    assert "grade_indirectness_downgrade" not in direct
    assert indirect["grade_indirectness_downgrade"] is True
    assert indirect["evidence_directness"] == "indirect"


def test_lint_blocks_surrogate_as_clinical_benefit():
    flags = es.assess({"outcomes": [{"field": "ORR", "results": [{"group": "ALL", "k": 10, "I2": 20.0}]}]})["flags"]
    viol = es.lint_conclusion("Treatment X improves overall survival in this population.", flags)
    assert any(v["rule"] == "surrogate_as_benefit" for v in viol)


def test_lint_allows_benefit_with_definitive_endpoint():
    flags = es.assess({"hr_outcomes": [{"field": "Overall survival HR",
                                        "results": [{"group": "ALL", "k": 5, "I2": 10.0}]}]})["flags"]
    viol = es.lint_conclusion("Treatment X improves overall survival.", flags)
    assert not any(v["rule"] == "surrogate_as_benefit" for v in viol)


def test_lint_blocks_superiority_without_direct_comparative():
    # only an INDIRECT HR present -> superiority claim is unsupported
    flags = es.assess({"hr_outcomes": [{"field": "pfs_hr", "results": [{"group": "ALL", "k": 5, "I2": 10.0}]}]},
                      indirect=True)["flags"]
    viol = es.lint_conclusion("X is superior to Y.", flags)
    assert any(v["rule"] == "unsupported_superiority" for v in viol)


def test_generic_free_survival_is_surrogate():
    for ep in ("metastasis-free survival", "leukemia-free survival", "failure-free survival",
               "recurrence-free survival"):
        assert es.classify_endpoint(ep) == "surrogate"
    assert es.classify_endpoint("overall survival") == "definitive"   # not caught by *-free


def test_lint_blocks_survival_benefit_phrasing():
    flags = es.assess({"outcomes": [{"field": "ORR", "results": [{"group": "ALL", "k": 10, "I2": 20.0}]}]})["flags"]
    viol = es.lint_conclusion("Treatment X shows a survival benefit.", flags)
    assert any(v["rule"] == "surrogate_as_benefit" for v in viol)


def test_lint_blocks_benefit_backed_only_by_indirect_definitive():
    # an INDIRECT overall-survival HR is not strong enough to assert clinical benefit
    flags = es.assess({"hr_outcomes": [{"field": "Overall survival HR",
                                        "results": [{"group": "ALL", "k": 5, "I2": 10.0}]}]},
                      indirect=True)["flags"]
    viol = es.lint_conclusion("Treatment X improves overall survival.", flags)
    assert any(v["rule"] == "surrogate_as_benefit" for v in viol)
    # the same OS endpoint as DIRECT evidence does permit the claim
    direct = es.assess({"hr_outcomes": [{"field": "Overall survival HR",
                                         "results": [{"group": "ALL", "k": 5, "I2": 10.0}]}]},
                       indirect=False)["flags"]
    assert es.lint_conclusion("Treatment X improves overall survival.", direct) == []


def test_lint_does_not_flag_negated_benefit():
    flags = es.assess({"outcomes": [{"field": "ORR", "results": [{"group": "ALL", "k": 10, "I2": 20.0}]}]})["flags"]
    for txt in ("There was no survival benefit.", "X did not improve survival."):
        assert es.lint_conclusion(txt, flags) == []


def test_lint_allows_superiority_with_direct_comparative():
    flags = es.assess({"hr_outcomes": [{"field": "pfs_hr", "results": [{"group": "ALL", "k": 5, "I2": 10.0}]}]},
                      indirect=False)["flags"]
    viol = es.lint_conclusion("X is more effective than Y.", flags)
    assert not any(v["rule"] == "unsupported_superiority" for v in viol)
