"""Regression tests for the deep-dive (Pass 1) bug fixes."""
from core.connectors.base import merge_dedup
from core.connectors.imported import to_citation
from core.models import Citation, IdType


def _c(id_type, id, **kw):
    return Citation(id_type=id_type, id=id, source=kw.pop("source", "s"), **kw)


def test_merge_dedup_unions_bridged_clusters():
    # PMID-only + DOI-only + a record carrying BOTH must collapse to ONE study
    # (union-find), not orphan the PMID-only cluster.
    r1 = _c(IdType.PMID, "111", source="pubmed")
    r2 = _c(IdType.DOI, "10.1/x", source="crossref")
    r3 = _c(IdType.PMID, "111", doi="10.1/x", source="scopus")
    out = merge_dedup([r1, r2, r3])
    assert len(out) == 1
    ids = out[0].extra_ids
    assert ids.get("PMID") == "111" and (out[0].doi == "10.1/x" or ids.get("DOI") == "10.1/x")


def test_merge_dedup_keeps_distinct_studies_sharing_title_prefix():
    # Two DIFFERENT PMIDs whose titles share the first 80 chars + year must NOT merge.
    t = "A randomized double-blind placebo-controlled trial of drug X in disease Y over 52 weeks part"
    a = _c(IdType.PMID, "900", title=t + " ONE", year=2022)
    b = _c(IdType.PMID, "901", title=t + " TWO", year=2022)
    assert len(merge_dedup([a, b])) == 2


def test_merge_dedup_idless_records_still_link_by_title():
    # Records with NO stable id still dedup by title+year (the fuzzy key is preserved).
    a = _c(IdType.URL, "u1", title="Same exact title", year=2021)
    b = _c(IdType.URL, "u2", title="Same exact title", year=2021)
    assert len(merge_dedup([a, b])) == 1


def test_merge_dedup_preserves_attested_verification():
    # An attested import folded into an unverified live record keeps attestation
    # (live > attested > unverified), so cite-or-omit doesn't drop a real imported study.
    live = _c(IdType.PMID, "111", source="pubmed", verification="unverified")
    imp = _c(IdType.PMID, "111", source="Embase", verification="unverified",
             provenance="embase.ris")
    imp.verification = "attested"
    out = merge_dedup([live, imp])
    assert len(out) == 1 and out[0].verification == "attested"


def test_import_id_is_deterministic():
    # Same title -> same id across calls (no per-process hash salt).
    rec = {"title": "An untitled preprint with no ids"}
    a = to_citation(rec, "GreyLit", "f.ris")
    b = to_citation(rec, "GreyLit", "f.ris")
    assert a.id == b.id and a.id.startswith("import:")


def test_merge_dedup_transitive_conflict_not_collapsed():
    # A bridges-via-title case where the two bridged clusters carry CONFLICTING PMIDs
    # must NOT collapse (the union step re-checks pairwise).
    t = "A randomized double-blind placebo-controlled trial of drug X in disease Y for 52w extra"
    a = _c(IdType.PMID, "111", doi="10.1/da", title=t + " a", year=2019)   # study #1
    b = _c(IdType.PMID, "222", title=t + " b", year=2019)                  # DIFFERENT study
    c = _c(IdType.DOI, "10.1/da", title=t + " c", year=2019)               # import copy of #1
    out = merge_dedup([a, b, c])
    pmids = {o.extra_ids.get("PMID", o.id if o.id_type == IdType.PMID else "") for o in out}
    assert len(out) == 2 and "111" in pmids and "222" in pmids


def test_is_rct_excludes_quasi_and_unrandomized():
    from core.sr.grade import _is_rct
    assert _is_rct("RCT") and _is_rct("randomized controlled trial") and _is_rct("randomised")
    assert not _is_rct("quasi-randomized trial")
    assert not _is_rct("unrandomized cohort")
    assert not _is_rct("non-randomized study")
    assert not _is_rct("prospective cohort")


def test_curate_predicates_dedup_multi_report_binary_and_continuous():
    # Two data-cuts of ONE trial (same trial_id, different cut) each with a clean 2x2 /
    # clean two-arm continuous row must curate down to ONE, so pooling doesn't double-count.
    from core.sr.models import ExtractedStudy, ExtractedValue
    from core.sr.stats.prepare import binary_poolable, continuous_poolable, curate_for_pool

    def _es(pmid, cut, vals):
        return ExtractedStudy(pmid=pmid, study_name="T", trial_id="NCT01", data_cut=cut,
                              modality="x", values=[ExtractedValue(**v) for v in vals])

    pair_b = {"events_tx": "e_tx", "n_tx": "n_tx", "events_ctrl": "e_ct", "n_ctrl": "n_ct"}
    b_vals = [{"field": "e_tx", "group": "g", "value": "5"}, {"field": "n_tx", "group": "g", "value": "50"},
              {"field": "e_ct", "group": "g", "value": "8"}, {"field": "n_ct", "group": "g", "value": "50"}]
    ex = [_es("111", "Jan 2024", b_vals), _es("222", "Nov 2025", b_vals)]
    cur, drops = curate_for_pool(ex, "fdeath", is_poolable=lambda es: binary_poolable(es, pair_b))
    assert len(cur) == 1 and len(drops) == 1   # superseded cut dropped

    pair_c = {"n_tx": "n_tx", "mean_tx": "m_tx", "sd_tx": "s_tx",
              "n_ctrl": "n_ct", "mean_ctrl": "m_ct", "sd_ctrl": "s_ct"}
    c_vals = [{"field": "n_tx", "group": "g", "value": "40"}, {"field": "m_tx", "group": "g", "value": "10"},
              {"field": "s_tx", "group": "g", "value": "2"}, {"field": "n_ct", "group": "g", "value": "42"},
              {"field": "m_ct", "group": "g", "value": "9"}, {"field": "s_ct", "group": "g", "value": "2"}]
    exc = [_es("111", "Jan 2024", c_vals), _es("222", "Nov 2025", c_vals)]
    cur2, drops2 = curate_for_pool(exc, "bw", is_poolable=lambda es: continuous_poolable(es, pair_c))
    assert len(cur2) == 1 and len(drops2) == 1
