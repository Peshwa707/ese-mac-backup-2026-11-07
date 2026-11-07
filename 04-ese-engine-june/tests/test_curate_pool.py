"""WP5 — curate effect estimates before pooling (per-field trial dedup + comparator anchor)."""
from core.sr.models import ExtractedStudy, ExtractedValue
from core.sr.stats.prepare import curate_for_pool

HR = "pfs_hr_vs_comparator"


def _es(pmid, study_name="", data_cut="", comparator=None, fields=(HR,)):
    vals = [ExtractedValue(field=f, group="efficacy", value="0.50 (0.30-0.80)", flag="", quote="q")
            for f in fields]
    if comparator is not None:
        vals.append(ExtractedValue(field="comparator_label", group="population",
                                   value=comparator, flag="", quote="q"))
    return ExtractedStudy(pmid=pmid, study_name=study_name, data_cut=data_cut, values=vals)


def test_dedup_keeps_most_recent_datacut_for_a_field():
    rows = [_es("old", "CARTITUDE-4", "2023"), _es("new", "CARTITUDE-4", "2026")]
    kept, dropped = curate_for_pool(rows, HR)
    assert {e.pmid for e in kept} == {"new"}
    assert dropped[0]["pmid"] == "old" and dropped[0]["field"] == HR


def test_dedup_groups_by_trial_id_over_study_name():
    # Round-2 finding #1: two reports of one trial whose study_name differs (e.g. acronym
    # vs registry title) but share the canonical trial_id must collapse to one estimate.
    old = ExtractedStudy(pmid="old", study_name="ACME-4", trial_id="NCT00000001", data_cut="2023",
                         values=[ExtractedValue(field=HR, group="efficacy",
                                                value="0.50 (0.30-0.80)", flag="", quote="q")])
    new = ExtractedStudy(pmid="new", study_name="A Phase 3 Study of ACME", trial_id="NCT00000001",
                         data_cut="2026",
                         values=[ExtractedValue(field=HR, group="efficacy",
                                                value="0.45 (0.28-0.72)", flag="", quote="q")])
    kept, dropped = curate_for_pool([old, new], HR)
    assert {e.pmid for e in kept} == {"new"}        # one trial, most-recent cut kept
    assert dropped[0]["pmid"] == "old"


def test_data_cut_key_parses_dates_without_eating_year_digits():
    # Round-3 finding #2: the year must never be mistaken for the day, day-before-month
    # and numeric/range forms must parse, and a range takes the LATER cut.
    from core.sr.stats.prepare import _data_cut_key

    def k(s):
        return _data_cut_key(ExtractedStudy(pmid="p", data_cut=s))

    assert k("Nov 2026") == (2026, 11, 0)          # "20" of 2026 is NOT the day
    assert k("15 Nov 2026") == (2026, 11, 15)      # day before month
    assert k("2026 Nov 15") == (2026, 11, 15)      # day after month
    assert k("September 2025") == (2025, 9, 0)
    assert k("05/2025") == (2025, 5, 0)            # numeric MM/YYYY, no day
    assert k("2025-05") == (2025, 5, 0)            # numeric YYYY-MM
    assert k("02/2022-05/2025") == (2025, 5, 0)    # range -> later cut
    assert k("Jan 2026 - Nov 2026") == (2026, 11, 0)  # same-year range -> later month
    assert k("2024-10 to 2025-05") == (2025, 5, 0)    # ISO range with 'to' -> later cut
    assert k("33.6 months") == (-1, 0, 0)          # no year -> unsortable sentinel
    assert k("") == (-1, 0, 0)
    # ordering: a dated cut beats a year-only cut in the same year
    assert k("Nov 2026") > k("Feb 2026") > k("2026")


def test_dedup_does_not_merge_studies_with_placeholder_trial_id():
    # Round-3 finding #3: placeholder trial_id values ("NR") are not canonical identity
    # and must NOT collapse two distinct studies into one.
    a = ExtractedStudy(pmid="a", study_name="ALPHA-TRIAL", trial_id="Not reported", data_cut="2026",
                       values=[ExtractedValue(field=HR, group="efficacy",
                                              value="0.50 (0.30-0.80)", flag="", quote="q")])
    b = ExtractedStudy(pmid="b", study_name="BETA-TRIAL", trial_id="N/A", data_cut="2026",
                       values=[ExtractedValue(field=HR, group="efficacy",
                                              value="0.60 (0.40-0.90)", flag="", quote="q")])
    kept, _ = curate_for_pool([a, b], HR)
    assert {e.pmid for e in kept} == {"a", "b"}     # two distinct trials survive


def test_dedup_resolves_same_year_cut_by_month():
    # Round-2 finding #2: two cuts in the same calendar year resolve by month (latest wins)
    # rather than first-seen order.
    feb = ExtractedStudy(pmid="feb", study_name="ACME-1", data_cut="Feb 2026", values=[
        ExtractedValue(field=HR, group="efficacy", value="0.50 (0.30-0.80)", flag="", quote="q")])
    nov = ExtractedStudy(pmid="nov", study_name="ACME-1", data_cut="2026 Nov 15", values=[
        ExtractedValue(field=HR, group="efficacy", value="0.45 (0.28-0.72)", flag="", quote="q")])
    kept, _ = curate_for_pool([feb, nov], HR)          # feb listed first
    assert {e.pmid for e in kept} == {"nov"}           # later month wins despite order


def test_per_field_keeps_older_report_when_newer_lacks_the_field():
    # Codex blocker: newest report omits this outcome; older report has it -> must keep older.
    older = _es("old", "TRIAL-X", "2023", fields=(HR,))           # has HR
    newer = _es("new", "TRIAL-X", "2026", fields=("orr",))         # newer cut, NO HR
    kept, _ = curate_for_pool([older, newer], HR)
    assert {e.pmid for e in kept} == {"old"}   # the valid HR estimate is NOT deleted


def test_anchor_fails_closed_on_missing_comparator():
    rows = [_es("a", "T", "2026", comparator=None)]   # no comparator_label
    kept, dropped = curate_for_pool(rows, HR, anchor_terms=["dpd", "pvd", "standard"])
    assert kept == []                                  # missing comparator -> dropped (fail closed)
    assert "missing" in dropped[0]["reason"].lower()


def test_anchor_drops_non_matching_comparator():
    rows = [_es("good", "T1", "2026", comparator="PVd or DPd standard of care"),
            _es("bad", "T2", "2026", comparator="GSI (crenigacestat)")]
    kept, _ = curate_for_pool(rows, HR, anchor_terms=["dpd", "pvd", "standard"])
    assert {e.pmid for e in kept} == {"good"}


def test_no_anchor_terms_skips_comparator_filter():
    rows = [_es("a", "T", "2026", comparator="GSI")]
    kept, _ = curate_for_pool(rows, HR)   # proportions/no-anchor: keep regardless of comparator
    assert {e.pmid for e in kept} == {"a"}


def test_records_without_field_are_ignored_not_dropped():
    rows = [_es("a", "T1", "2026", fields=(HR,)), _es("b", "T2", "2026", fields=("orr",))]
    kept, dropped = curate_for_pool(rows, HR)
    assert {e.pmid for e in kept} == {"a"}     # b simply absent from this pool
    assert all(d["pmid"] != "b" for d in dropped)   # b not "dropped", just irrelevant


def test_newer_unparseable_cut_does_not_evict_older_parseable_hr():
    # Codex repro: old 2024 'HR 0.50 (0.30-0.80)' (parseable) + new 2026 'HR 0.50'
    # (no CI, unparseable for HR pooling). Curating with the HR-poolable predicate must
    # keep the OLD parseable estimate, not drop it for the unparseable newer cut.
    from core.sr.stats.prepare import hr_poolable
    old = ExtractedStudy(pmid="old", study_name="TRIAL-X", data_cut="2024", values=[
        ExtractedValue(field=HR, group="efficacy", value="0.50 (0.30-0.80)", flag="", quote="q")])
    new = ExtractedStudy(pmid="new", study_name="TRIAL-X", data_cut="2026", values=[
        ExtractedValue(field=HR, group="efficacy", value="0.50", flag="", quote="q")])  # unparseable
    kept, _ = curate_for_pool([old, new], HR, is_poolable=lambda es: hr_poolable(es, HR))
    assert {e.pmid for e in kept} == {"old"}   # the usable estimate survives


def test_anchored_vertical_config_actually_enables_comparator_gate():
    # The real anchored vertical must define anchor_terms, or the gate is inert.
    from core.sr.config import load_sr_config
    cfg = load_sr_config("mm-bcma-early-relapse")
    assert cfg.indirect_comparison is True
    assert cfg.anchor_terms, "anchored vertical must set synthesis.anchor_terms"
    assert "dpd" in cfg.anchor_terms and "pvd" in cfg.anchor_terms
    # anchor terms must be SPECIFIC regimens, not generic labels that pass any comparator
    for generic in ("standard of care", "physician", "investigator"):
        assert generic not in cfg.anchor_terms, f"generic anchor term leaked: {generic}"
    # a bare generic comparator must NOT pass the gate; a named regimen must
    from core.sr.models import ExtractedStudy, ExtractedValue
    from core.sr.stats.prepare import curate_for_pool
    def es(pmid, comp):
        return ExtractedStudy(pmid=pmid, study_name=pmid, data_cut="2026", values=[
            ExtractedValue(field=HR, group="efficacy", value="0.5 (0.3-0.8)", flag="", quote="q"),
            ExtractedValue(field="comparator_label", group="population", value=comp, flag="", quote="q")])
    kept, _ = curate_for_pool(
        [es("generic", "investigator's choice"), es("named", "physician's choice PVd or DPd")],
        HR, anchor_terms=cfg.anchor_terms)
    assert {e.pmid for e in kept} == {"named"}   # bare "investigator's choice" rejected


def test_meta_analyze_warns_when_anchored_without_anchor_terms(monkeypatch, tmp_path):
    import core.sr.stats.run as stats_run

    class Cfg:
        subgroup_field = ""
        subgroup_map = None
        dose_outcome = ""
        anchor_terms: list = []
        indirect_comparison = True

        def poolable_fields(self):
            return {"proportion": [], "log_effect": []}

        def binary_2x2_fields(self):
            return []

        def continuous_fields(self):
            return []

    monkeypatch.setattr(stats_run, "r_available", lambda: False)
    res = stats_run.meta_analyze([], {}, tmp_path, cfg=Cfg())
    assert any("anchor_terms" in n and "INACTIVE" in n for n in [res.note] if isinstance(res.note, str)) \
        or "anchor_terms" in (res.note or "")
