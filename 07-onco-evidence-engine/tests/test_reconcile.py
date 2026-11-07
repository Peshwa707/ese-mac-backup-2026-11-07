"""WP6 — reviewer reconciliation: Cohen's kappa (nominal + weighted) + union consensus."""
from core.sr.reconcile import cohen_kappa, reconcile, ORDER


def test_perfect_agreement_kappa_one():
    a = ["include", "exclude", "uncertain"]
    assert cohen_kappa(a, a) == 1.0


def test_weighted_kappa_exceeds_nominal_for_adjacent_disagreement():
    # genuine agreement (include+exclude) plus ADJACENT exclude<->uncertain disagreements;
    # ordinal weighting gives partial credit for the adjacent splits -> weighted > nominal.
    a = ["include"] * 40 + ["exclude"] * 60
    b = ["include"] * 40 + ["exclude"] * 50 + ["uncertain"] * 10
    nom = cohen_kappa(a, b, order=ORDER)
    wt = cohen_kappa(a, b, weights="linear", order=ORDER)
    assert wt > nom and 0 < nom < wt < 1


def test_empty_or_mismatched_returns_zero():
    assert cohen_kappa([], []) == 0.0
    assert cohen_kappa(["include"], []) == 0.0


def test_weighted_default_order_uses_screening_scale_not_alphabetical():
    # All 3 categories present, with uncertain<->exclude disagreements. Under the correct
    # ORDER those are ADJACENT (partial credit); under alphabetical (exclude,include,
    # uncertain) they are MAXIMALLY distant (no credit). So the default must equal the
    # explicit-ORDER result and DIFFER from the old alphabetical behavior.
    a = ["include"] * 30 + ["exclude"] * 30 + ["uncertain"] * 40
    b = ["include"] * 30 + ["exclude"] * 30 + ["exclude"] * 40   # uncertain(a) vs exclude(b)
    default = cohen_kappa(a, b, weights="linear")                 # order=None -> should use ORDER
    explicit = cohen_kappa(a, b, weights="linear", order=ORDER)
    alphabetical = cohen_kappa(a, b, weights="linear", order=sorted(set(a) | set(b)))
    assert default == explicit
    assert default != alphabetical    # proves the fix actually changed behavior


def test_weighted_without_order_fails_closed_for_nonstandard_categories():
    import pytest
    with pytest.raises(ValueError):
        cohen_kappa(["yes", "no"], ["yes", "maybe"], weights="linear")  # not screening cats


def test_union_consensus_advances_on_either_include_or_uncertain():
    out = reconcile({"x": "exclude"}, {"x": "include"})
    assert "x" in out["advance"] and "x" not in out["exclude_both"]
    out2 = reconcile({"y": "exclude"}, {"y": "uncertain"})
    assert "y" in out2["advance"]


def test_both_exclude_is_excluded():
    out = reconcile({"z": "exclude"}, {"z": "exclude"})
    assert out["exclude_both"] == ["z"] and out["advance"] == []


def test_missing_id_defaults_uncertain_and_advances():
    out = reconcile({"a": "exclude"}, {})   # b has no decision -> uncertain -> advance
    assert "a" in out["advance"]


def test_hard_conflicts_counted():
    out = reconcile({"a": "include", "b": "exclude"}, {"a": "exclude", "b": "exclude"})
    assert out["n_hard_conflicts"] == 1     # 'a' is include-vs-exclude
    assert any(d["id"] == "a" and d["hard"] for d in out["disagreements"])


def test_reports_both_kappas():
    out = reconcile({"a": "include", "b": "exclude"}, {"a": "include", "b": "uncertain"})
    assert "kappa_nominal" in out and "kappa_weighted" in out
    assert out["kappa_weighted"] >= out["kappa_nominal"]   # adjacent disagreement
