"""Tests for review-type profiles and profile-gated manuscript assembly."""

from __future__ import annotations

from core.sr.config import load_sr_config
from core.sr.manuscript.assemble import build_components
from core.sr.profile import load_profile, profile_names
from core.sr.models import GradeRow, RoBAssessment, RoBDomain
from tests.test_sr_manuscript import _RECORDS, _synthetic_run


def test_profiles_registry():
    assert set(profile_names()) >= {"systematic", "scoping", "rapid", "living"}
    sysrev = load_profile()                       # default
    assert sysrev.name == "systematic" and sysrev.framing == "pico"
    assert sysrev.enabled("rob") and sysrev.enabled("meta") and sysrev.enabled("grade")
    scoping = load_profile("scoping")
    assert scoping.framing == "pcc"
    assert not scoping.enabled("rob") and not scoping.enabled("grade") and not scoping.enabled("meta")
    assert scoping.enabled("chart")


def test_systematic_includes_analysis_components():
    rob = [RoBAssessment(pmid="111", tool="rob2", overall="Low", domains=[RoBDomain(key="D1", judgment="Low")])]
    grade = [GradeRow(outcome="ORR", certainty="Moderate")]
    keys = [c.key for c in build_components(_synthetic_run(), load_sr_config(), _RECORDS,
                                            rob=rob, grade=grade, profile=load_profile("systematic"))]
    assert "06_risk_of_bias" in keys and "08_grade" in keys


def test_scoping_omits_rob_grade_meta():
    rob = [RoBAssessment(pmid="111", tool="rob2", overall="Low")]
    grade = [GradeRow(outcome="ORR", certainty="Moderate")]
    keys = [c.key for c in build_components(_synthetic_run(), load_sr_config(), _RECORDS,
                                            rob=rob, grade=grade, profile=load_profile("scoping"))]
    assert "06_risk_of_bias" not in keys
    assert "07_statistical_analysis" not in keys
    assert "08_grade" not in keys
    # Core narrative/data components remain.
    assert "04_included_abstracts" in keys and "11_supplementary" in keys
