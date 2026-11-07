"""Few-shot adjudicated-exemplar injection in screening prompts (otto-SR technique #5).

Improvement over srma's generic generated CoT exemplars: examples are curated from our
OWN adjudicated hard/boundary cases (configured per vertical), injected before the
records. Off by default (empty list) so the prompt is byte-identical to today.
"""
from __future__ import annotations

from core.models import Citation, IdType
from core.sr.config import SRConfig, load_sr_config
from core.sr.screen import build_screening_prompt


def _rec(pmid):
    return Citation(id_type=IdType.PMID, id=pmid, source="pubmed", title="t", snippet="abstract text")


def test_no_examples_prompt_is_unchanged():
    cfg = load_sr_config()
    _, user = build_screening_prompt([_rec("1")], cfg)
    assert "WORKED EXAMPLE" not in user           # default vertical has none -> no block


def test_examples_injected_before_records(monkeypatch):
    cfg = load_sr_config()
    examples = [{
        "title": "Phase 1 dose-escalation of an off-target agent",
        "abstract": "A first-in-human study of a GPRC5D bispecific...",
        "decision": "exclude",
        "reason": "Off-target (GPRC5D, not BCMA) — fails the intervention criterion.",
    }]
    monkeypatch.setattr(SRConfig, "screening_few_shot_examples", property(lambda self: examples))
    _, user = build_screening_prompt([_rec("1")], cfg)
    assert "WORKED EXAMPLE" in user
    assert "GPRC5D bispecific" in user
    assert "Off-target (GPRC5D, not BCMA)" in user
    # examples must appear BEFORE the records block (context the model reads first)
    assert user.index("WORKED EXAMPLE") < user.index("<records>")


def test_config_few_shot_examples_parsing():
    prop = SRConfig.screening_few_shot_examples.fget

    class _Fake:
        pico = {"screening": {"few_shot_examples": [
            {"title": "t", "abstract": "a", "decision": "include", "reason": "r"}]}}
    assert len(prop(_Fake())) == 1
    _Fake.pico = {}
    assert prop(_Fake()) == []                     # default: no examples
    # The real default vertical configures none.
    assert load_sr_config().screening_few_shot_examples == []
