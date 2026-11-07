"""WP-M2.2 — reviewer roster resolution + independence preflight."""
import pytest

from core.llm.gateway import ProviderSpec
from core.sr.orchestrator import reviewers as rv
from core.sr.orchestrator.reviewers import ReviewerIndependenceError


class _Cfg:
    def __init__(self, roster=None):
        self.reviewers = roster or {}


def test_defaults_r1_anthropic_r2_local():
    cfg = _Cfg()
    r1 = rv.resolve_reviewer(cfg, "r1")
    r2 = rv.resolve_reviewer(cfg, "r2")
    assert r1.source == "anthropic" and "claude" in r1.model
    assert r2.source == "local" and r2.endpoint


def test_roster_override():
    cfg = _Cfg({"r2": {"source": "local", "model": "llama3.1:70b",
                       "endpoint": "http://localhost:11434"}})
    r2 = rv.resolve_reviewer(cfg, "r2")
    assert r2.model == "llama3.1:70b"


def test_partial_override_of_known_reviewer_keeps_default_model():
    cfg = _Cfg({"r1": {"source": "anthropic"}})    # model inherited from defaults
    assert rv.resolve_reviewer(cfg, "r1").model == "claude-opus-4-8"


def test_unknown_reviewer_missing_fields_fails_closed():
    cfg = _Cfg({"r3": {"source": "anthropic"}})    # no defaults for r3, no model -> fail closed
    with pytest.raises(ValueError, match="missing source/model"):
        rv.resolve_reviewer(cfg, "r3")


def test_default_r1_r2_are_independent():
    cfg = _Cfg()
    rv.assert_independent(rv.resolve_reviewer(cfg, "r1"), rv.resolve_reviewer(cfg, "r2"))  # no raise


def test_identical_reviewers_raise():
    s = ProviderSpec(source="anthropic", model="claude-opus-4-8")
    with pytest.raises(ReviewerIndependenceError, match="identical"):
        rv.assert_independent(s, s)


def test_same_family_raises():
    r1 = ProviderSpec(source="anthropic", model="claude-opus-4-8")
    r2 = ProviderSpec(source="anthropic", model="claude-sonnet-4-6")
    with pytest.raises(ReviewerIndependenceError, match="family"):
        rv.assert_independent(r1, r2)


def test_cross_family_passes():
    r1 = ProviderSpec(source="anthropic", model="claude-opus-4-8")
    r2 = ProviderSpec(source="local", model="llama3.1:70b", endpoint="http://localhost:11434")
    rv.assert_independent(r1, r2)                  # different families -> ok


def test_same_local_endpoint_raises_even_if_models_differ():
    r1 = ProviderSpec(source="local", model="llama3.1:70b", endpoint="http://localhost:11434")
    r2 = ProviderSpec(source="local", model="qwen2.5:72b", endpoint="http://localhost:11434")
    with pytest.raises(ReviewerIndependenceError, match="local endpoint"):
        rv.assert_independent(r1, r2)


def test_same_local_endpoint_detected_across_url_spellings():
    # trailing slash + localhost/127.0.0.1 must canonicalize to the same endpoint
    r1 = ProviderSpec(source="local", model="llama3.1:70b", endpoint="http://localhost:11434")
    r2 = ProviderSpec(source="local", model="qwen2.5:72b", endpoint="http://127.0.0.1:11434/")
    with pytest.raises(ReviewerIndependenceError, match="local endpoint"):
        rv.assert_independent(r1, r2)


def test_anthropic_aliases_without_claude_token_still_same_family():
    # aliases that omit the "claude" substring must still resolve to the claude family
    r1 = ProviderSpec(source="anthropic", model="opus-4-8")
    r2 = ProviderSpec(source="anthropic", model="sonnet-4-6")
    assert rv.model_family(r1) == rv.model_family(r2) == "claude"
    with pytest.raises(ReviewerIndependenceError, match="family"):
        rv.assert_independent(r1, r2)


def test_cross_provider_same_family_still_raises():
    # e.g. claude via anthropic API vs a hypothetical local claude mirror -> still same family
    r1 = ProviderSpec(source="anthropic", model="claude-opus-4-8")
    r2 = ProviderSpec(source="local", model="claude-3-local", endpoint="http://x")
    with pytest.raises(ReviewerIndependenceError):
        rv.assert_independent(r1, r2)
