"""Tests for NMA network-feasibility logic (gemtc-independent)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.sr.stats.nma import NMAEdge, assess_network, build_nma_edges, nma_available, run_nma

_TARGET = ("CAR-T", "BsAb")


def _edge(study, trt, comp):
    return NMAEdge(study=study, treatment=trt, comparator=comp, diff=-0.5, se=0.2)


def test_assess_connected_network():
    edges = [_edge("S1", "CAR-T", "SoC"), _edge("S2", "BsAb", "SoC")]   # connected via SoC
    ok, reason, nodes = assess_network(edges, _TARGET)
    assert ok and {"CAR-T", "BsAb", "SoC"} <= nodes


def test_assess_disconnected_network():
    edges = [_edge("S1", "CAR-T", "SoC-A"), _edge("S2", "BsAb", "SoC-B")]  # no shared node
    ok, reason, _ = assess_network(edges, _TARGET)
    assert not ok and "disconnected" in reason.lower()


def test_assess_empty_network_is_honest():
    ok, reason, nodes = assess_network([], _TARGET)
    assert not ok and "comparator" in reason.lower() and nodes == set()


def test_assess_network_without_configured_target_is_honest():
    edges = [_edge("S1", "Drug A", "Placebo"), _edge("S2", "Drug B", "Placebo")]
    ok, reason, nodes = assess_network(edges)
    assert not ok
    assert "configured two-arm" in reason
    assert {"Drug A", "Drug B", "Placebo"} <= nodes


def test_run_nma_infeasible_returns_reason(tmp_path):
    res = run_nma([], tmp_path, target=_TARGET)
    assert res.feasible is False and res.reason and res.league == []


def test_run_nma_connected_without_gemtc(tmp_path, monkeypatch):
    import core.sr.stats.nma as nma
    monkeypatch.setattr(nma, "nma_available", lambda: False)
    res = run_nma([_edge("S1", "CAR-T", "SoC"), _edge("S2", "BsAb", "SoC")], tmp_path,
                  target=_TARGET)
    assert res.feasible is False and "gemtc" in res.reason.lower()


def test_build_nma_edges_empty_without_comparator():
    from core.sr.models import ExtractedStudy, ExtractedValue
    # Has an HR but NO comparator_label -> no edge (network stays infeasible, honest).
    es = ExtractedStudy(pmid="1", modality="CAR-T", values=[
        ExtractedValue(field="pfs_hr_vs_comparator", group="efficacy", value="HR 0.42 (95% CI 0.26-0.68)")])
    assert build_nma_edges([es], {}) == []


def test_build_nma_edges_connects_via_comparator_label():
    from core.sr.models import ExtractedStudy, ExtractedValue

    def study(pmid, mod, comp):
        return ExtractedStudy(pmid=pmid, modality=mod, values=[
            ExtractedValue(field="comparator_label", group="population", value=comp),
            ExtractedValue(field="pfs_hr_vs_comparator", group="efficacy",
                           value="HR 0.42 (95% CI 0.26-0.68)")])
    # CAR-T vs "standard of care" and BsAb vs "SoC" -> both normalize to SoC -> connected.
    edges = build_nma_edges([study("A", "CAR-T", "standard of care"), study("B", "BsAb", "SoC")], {})
    assert len(edges) == 2 and {e.comparator for e in edges} == {"SoC"}
    ok, reason, nodes = assess_network(edges, _TARGET)
    assert ok and {"CAR-T", "BsAb", "SoC"} <= nodes


def test_norm_comparator_keeps_distinct_regimens():
    from core.sr.stats.nma import _norm_comparator
    assert _norm_comparator("Physician's choice") == "SoC"
    assert _norm_comparator("placebo plus GDMT") == "placebo"
    assert _norm_comparator("PVd") == "PVd"            # named regimen NOT lumped


@pytest.mark.skipif(not nma_available(), reason="gemtc/JAGS not installed (set SR_RSCRIPT to CRAN R)")
def test_live_gemtc_nma_runs(tmp_path):
    # End-to-end Bayesian NMA on a connected network; node names with hyphens must
    # be sanitized for gemtc and mapped back in the league.
    edges = [_edge("S1", "CAR-T", "SoC"), _edge("S2", "BsAb", "SoC"), _edge("S3", "CAR-T", "SoC")]
    res = run_nma(edges, tmp_path, target=_TARGET)
    assert res.feasible, res.reason
    comps = {r["comparison"] for r in res.league}
    assert any("CAR-T" in c and "BsAb" in c for c in comps)   # the indirect estimate exists
