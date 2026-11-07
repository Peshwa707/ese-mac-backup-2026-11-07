"""Tests for hazard-ratio parsing and log-HR preparation."""

from __future__ import annotations

import math

from core.sr.models import ExtractedStudy, ExtractedValue
from core.sr.stats.prepare import hr_rows, parse_hr_ci


def test_parse_hr_ci():
    assert parse_hr_ci("HR 0.42 (95% CI 0.26-0.68); p=0.0004") == (0.42, 0.26, 0.68)
    assert parse_hr_ci("hazard ratio 0.11 [95% CI 0.05-0.22] ITT") == (0.11, 0.05, 0.22)
    assert parse_hr_ci("HRs 0.11-0.51 vs comparators") is None   # ambiguous range, not HR+CI
    assert parse_hr_ci("not reported") is None


def test_hr_rows_computes_log_hr():
    es = ExtractedStudy(pmid="1", modality="CAR-T", values=[
        ExtractedValue(field="pfs_hr_vs_comparator", group="efficacy", value="HR 0.42 (95% CI 0.26-0.68)")])
    rows, excluded = hr_rows([es], "pfs_hr_vs_comparator", {})
    assert len(rows) == 1 and excluded == 0
    r = rows[0]
    assert abs(r.yi - math.log(0.42)) < 1e-9
    expected_se = (math.log(0.68) - math.log(0.26)) / (2 * 1.96)
    assert abs(r.vi - expected_se ** 2) < 1e-9 and r.modality == "CAR-T"


def test_hr_rows_excludes_unparseable():
    es = ExtractedStudy(pmid="2", modality="BsAb", values=[
        ExtractedValue(field="pfs_hr_vs_comparator", group="efficacy", value="HRs 0.11-0.51")])
    rows, excluded = hr_rows([es], "pfs_hr_vs_comparator", {})
    assert rows == [] and excluded == 1
