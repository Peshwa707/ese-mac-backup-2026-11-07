import pytest

from screen_prove.contracts import IntegrityError, canonical_bytes, object_hash
from screen_prove.risk_of_bias import (
    JUDGMENTS,
    ROB_ASSESSMENT_VERSION,
    build_domain_judgment,
    build_rob_assessment,
    not_assessed_domain_judgment,
    verify_rob_assessment,
)


# --- build_domain_judgment (assessed, source-linked) ------------------------


def test_build_domain_judgment_accepts_every_documented_judgment_value():
    for value in JUDGMENTS:
        if value == "not_assessed":
            continue
        judgment = build_domain_judgment(
            "randomization_process", value, "rationale text", ["Methods p.2: computer-generated sequence"]
        )
        assert judgment["judgment"] == value
        assert judgment["domain"] == "randomization_process"
        assert judgment["source_links"] == ["Methods p.2: computer-generated sequence"]


def test_build_domain_judgment_rejects_an_unbounded_judgment_value():
    with pytest.raises(IntegrityError, match="unknown risk-of-bias judgment"):
        build_domain_judgment("randomization_process", "probably_fine", "rationale", [])


def test_build_domain_judgment_requires_a_non_empty_domain_name():
    with pytest.raises(IntegrityError, match="domain"):
        build_domain_judgment("", "low_risk", "rationale", [])


def test_build_domain_judgment_requires_a_non_empty_rationale():
    with pytest.raises(IntegrityError, match="rationale"):
        build_domain_judgment("randomization_process", "low_risk", "   ", [])


def test_build_domain_judgment_rejects_an_empty_source_link_entry():
    with pytest.raises(IntegrityError, match="source_links"):
        build_domain_judgment("randomization_process", "low_risk", "rationale", ["quote", "  "])


def test_build_domain_judgment_rejects_source_links_on_a_not_assessed_judgment():
    with pytest.raises(IntegrityError, match="not_assessed"):
        build_domain_judgment("randomization_process", "not_assessed", "insufficient detail", ["some quote"])


# --- not_assessed_domain_judgment (abstention/unknown state) ---------------


def test_not_assessed_domain_judgment_carries_no_source_links():
    judgment = not_assessed_domain_judgment("blinding_of_outcome_assessment", "not reported in the methods")
    assert judgment["judgment"] == "not_assessed"
    assert judgment["source_links"] == []
    assert judgment["rationale"] == "not reported in the methods"


def test_not_assessed_domain_judgment_still_requires_a_rationale():
    with pytest.raises(IntegrityError, match="rationale"):
        not_assessed_domain_judgment("blinding_of_outcome_assessment", "")


# --- build_rob_assessment / verify_rob_assessment ---------------------------


def _domains():
    return [
        build_domain_judgment(
            "randomization_process", "low_risk", "computer-generated sequence reported", ["Methods p.2"]
        ),
        not_assessed_domain_judgment("missing_outcome_data", "attrition not reported"),
    ]


def test_build_rob_assessment_is_byte_stable_for_identical_inputs():
    assessment_a = build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns")
    assessment_b = build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns")
    assert canonical_bytes(assessment_a) == canonical_bytes(assessment_b)


def test_verify_rob_assessment_accepts_a_valid_assessment():
    assessment = build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns")
    verified = verify_rob_assessment(assessment)
    assert verified["record_id"] == "record:1"
    assert len(verified["domains"]) == 2


def test_build_rob_assessment_rejects_empty_record_id():
    with pytest.raises(IntegrityError, match="record_id"):
        build_rob_assessment("", "protocol-rob-v1", _domains(), "some_concerns")


def test_build_rob_assessment_rejects_empty_tool():
    with pytest.raises(IntegrityError, match="tool"):
        build_rob_assessment("record:1", "", _domains(), "some_concerns")


def test_build_rob_assessment_rejects_no_domain_judgments():
    with pytest.raises(IntegrityError, match="at least one domain"):
        build_rob_assessment("record:1", "protocol-rob-v1", [], "some_concerns")


def test_build_rob_assessment_rejects_an_unbounded_overall_judgment():
    with pytest.raises(IntegrityError, match="unknown overall"):
        build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "probably_fine")


def test_build_rob_assessment_rejects_duplicate_domain_names():
    domains = [
        build_domain_judgment("randomization_process", "low_risk", "reason a", []),
        build_domain_judgment("randomization_process", "high_risk", "reason b", []),
    ]
    with pytest.raises(IntegrityError, match="duplicate"):
        build_rob_assessment("record:1", "protocol-rob-v1", domains, "high_risk")


def test_build_rob_assessment_rejects_an_invented_overall_judgment_when_every_domain_is_unassessed():
    domains = [
        not_assessed_domain_judgment("randomization_process", "not reported"),
        not_assessed_domain_judgment("missing_outcome_data", "not reported"),
    ]
    with pytest.raises(IntegrityError, match="not_assessed"):
        build_rob_assessment("record:1", "protocol-rob-v1", domains, "low_risk")


def test_build_rob_assessment_allows_not_assessed_overall_when_every_domain_is_unassessed():
    domains = [
        not_assessed_domain_judgment("randomization_process", "not reported"),
        not_assessed_domain_judgment("missing_outcome_data", "not reported"),
    ]
    assessment = build_rob_assessment("record:1", "protocol-rob-v1", domains, "not_assessed")
    assert assessment["overall_judgment"] == "not_assessed"


def test_build_rob_assessment_allows_an_assessed_overall_when_at_least_one_domain_is_assessed():
    assessment = build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns")
    assert assessment["overall_judgment"] == "some_concerns"


# --- verify_rob_assessment tamper detection (replay-time, not just build-time) --


def test_verify_rob_assessment_rejects_tampering_after_the_fact():
    assessment = build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns")
    assessment["domains"][0]["judgment"] = "high_risk"
    with pytest.raises(IntegrityError, match="rob_hash"):
        verify_rob_assessment(assessment)


def test_verify_rob_assessment_rejects_an_unsupported_version_with_a_recomputed_hash():
    assessment = dict(build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns"))
    assessment["version"] = "rob-assessment-v0"
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    assessment["rob_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="unsupported risk-of-bias assessment version"):
        verify_rob_assessment(assessment)


def test_verify_rob_assessment_rejects_duplicate_domains_with_a_recomputed_hash():
    assessment = dict(build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns"))
    duplicated = [dict(assessment["domains"][0]), dict(assessment["domains"][0])]
    assessment["domains"] = duplicated
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    assessment["rob_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="duplicate"):
        verify_rob_assessment(assessment)


def test_verify_rob_assessment_rejects_an_empty_record_id_with_a_recomputed_hash():
    assessment = dict(build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns"))
    assessment["record_id"] = ""
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    assessment["rob_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="record_id"):
        verify_rob_assessment(assessment)


def test_verify_rob_assessment_rejects_an_empty_tool_with_a_recomputed_hash():
    assessment = dict(build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns"))
    assessment["tool"] = ""
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    assessment["rob_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="tool"):
        verify_rob_assessment(assessment)


def test_verify_rob_assessment_rejects_an_invented_overall_judgment_with_a_recomputed_hash():
    domains = [
        not_assessed_domain_judgment("randomization_process", "not reported"),
        not_assessed_domain_judgment("missing_outcome_data", "not reported"),
    ]
    assessment = dict(build_rob_assessment("record:1", "protocol-rob-v1", domains, "not_assessed"))
    assessment["overall_judgment"] = "low_risk"
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    assessment["rob_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="not_assessed"):
        verify_rob_assessment(assessment)


def test_verify_rob_assessment_rejects_source_links_on_a_not_assessed_domain_with_a_recomputed_hash():
    assessment = dict(build_rob_assessment("record:1", "protocol-rob-v1", _domains(), "some_concerns"))
    tampered_domains = [dict(domain) for domain in assessment["domains"]]
    tampered_domains[1]["source_links"] = ["fabricated quote"]
    assessment["domains"] = tampered_domains
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    assessment["rob_hash"] = object_hash(body)
    with pytest.raises(IntegrityError, match="not_assessed"):
        verify_rob_assessment(assessment)
