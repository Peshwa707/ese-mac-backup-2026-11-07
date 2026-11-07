"""Parse messy extracted values into clean (events, n) rows for pooling.

Conservative by design: a study contributes to an outcome's pool only if BOTH a
single percentage for that outcome AND a total N are cleanly parseable. Anything
ambiguous is excluded and counted (reported honestly), never coerced.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from core.sr.models import ExtractedStudy
from core.sr.stats.impute import mean_sd_from_median_iqr, mean_sd_from_median_range

# Legacy compact form, e.g. "HR 0.42 (95% CI 0.26-0.68)" or "[0.05-0.22]".
_HR_RE = re.compile(
    r"(\d+\.\d+)\s*[\(\[]?\s*(?:95%?\s*ci[:,]?\s*)?(\d+\.\d+)\s*[-–to ]+\s*(\d+\.\d+)", re.I)

# Robust effect parsing for epidemiology formats. The point estimate may carry an
# RR/OR/HR label and =/:/, separators; the 95% CI pair may be split by -, –, comma,
# or the word "to": "HR = 1.17 (95% CI: 1.02, 1.34)", "OR, 2.06; 95% CI, 1.11-3.85",
# "RR 1.23, 95% CI = 1.03 to 1.46", "RR 2.68 (95% confidence interval 1.72-4.17)".
_EST_LABELLED = re.compile(
    r"\b(?:hazard ratio|odds ratio|risk ratio|rate ratio|relative risk|hr|or|rr)\s*[=:,]?\s*"
    r"\(?\s*(\d+\.?\d*)", re.I)
_EST_BEFORE_CI = re.compile(r"(\d+\.?\d*)\s*[,;]?\s*\(?\s*(?=95\s*%?\s*(?:ci|c\.i\.|confidence))", re.I)
_CI_PAIR = re.compile(
    r"95\s*%?\s*(?:ci|c\.i\.|confidence interval)\s*[=:,]?\s*\(?\s*"
    r"(\d+\.?\d*)\s*(?:[-–]|,|\bto\b)\s*(\d+\.?\d*)", re.I)

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_EVENTS_OF_N = re.compile(r"\b(\d[\d,]{0,6})\s*(?:/|\bof\b)\s*(\d[\d,]{0,6})\b", re.I)
_INT = re.compile(r"(\d[\d,]{0,6})")
_NUM = re.compile(r"(-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class ParsedProportion:
    pct: float
    events: int | None = None
    n: int | None = None


def canonical_subgroup(raw: str, subgroup_map: dict[str, list[str]] | None = None) -> str:
    """Normalize a free-text subgroup value to a canonical label.

    ``subgroup_map`` is ``{canonical_label: [match_substring, ...]}`` supplied by
    the active vertical. With no map (the default) this is the identity — the raw
    value passes through, so unrelated verticals are never coerced into another
    review's buckets.
    """
    s = (raw or "").strip()
    if not subgroup_map or not s:
        return s or "unclear"
    low = s.lower()
    for label, keys in subgroup_map.items():
        if any(k in low for k in keys):
            return label
    return s


def parse_proportion(s: str) -> ParsedProportion | None:
    raw = (s or "").replace(",", "")
    pairs = _EVENTS_OF_N.findall(raw)
    if len(pairs) == 1:
        events, n = (int(x) for x in pairs[0])
        if 0 <= events <= n and n > 0:
            return ParsedProportion(round(100.0 * events / n, 1), events, n)
        return None
    if len(pairs) > 1:
        return None

    pct_matches = [
        m.group(1) for m in _PCT.finditer(raw)
        if not (
            m.group(1) == "95"
            and re.match(r"\s*(?:ci|c\.i\.|confidence)", raw[m.end():], re.I)
        )
    ]
    if len(pct_matches) == 1:
        val = float(pct_matches[0])
        return ParsedProportion(val) if 0.0 <= val <= 100.0 else None
    if len(pct_matches) > 1:
        return None

    nums = [float(x) for x in _NUM.findall(raw)]
    if len(nums) == 1 and 0.0 <= nums[0] <= 1.0:
        return ParsedProportion(round(nums[0] * 100.0, 1))
    return None


def parse_pct(s: str) -> float | None:
    parsed = parse_proportion(s)
    return None if parsed is None else parsed.pct


def parse_int(s: str) -> int | None:
    m = _INT.search((s or "").replace(",", ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_number(s: str) -> float | None:
    m = _NUM.search((s or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


@dataclass
class PoolRow:
    pmid: str
    modality: str
    events: int
    n: int
    pct: float


def _real(v) -> bool:
    return v.flag != "NR" and (v.value or "").strip().upper() != "NR" \
        and not (v.value or "").strip().upper().startswith("ESTIMATED")


@dataclass
class HRRow:
    pmid: str
    modality: str
    hr: float
    yi: float       # log hazard ratio
    vi: float       # variance of log HR


@dataclass
class BinRow:
    pmid: str
    modality: str
    events_tx: int
    n_tx: int
    events_ctrl: int
    n_ctrl: int


@dataclass
class ContRow:
    pmid: str
    modality: str
    n_tx: int
    mean_tx: float
    sd_tx: float
    n_ctrl: int
    mean_ctrl: float
    sd_ctrl: float
    imputed: bool = False
    imputation_method: str = ""


@dataclass
class MetaRegRow:
    pmid: str
    yi: float
    vi: float
    moderator: float


def parse_hr_ci(s: str):
    """Parse an effect estimate + 95% CI -> (est, lo, hi), or None if not cleanly parseable.

    Handles trial-style ("HR 0.42 (95% CI 0.26-0.68)") and epidemiology-style formats
    ("HR = 1.17 (95% CI: 1.02, 1.34)", "OR, 2.06; 95% CI, 1.11-3.85", "RR 1.03 to 1.46").
    """
    s = s or ""
    ci = _CI_PAIR.search(s)
    if ci:
        est_m = _EST_LABELLED.search(s) or _EST_BEFORE_CI.search(s)
        if est_m:
            est, lo, hi = float(est_m.group(1)), float(ci.group(1)), float(ci.group(2))
            if 0 < lo <= est <= hi and hi <= 50:
                return est, lo, hi
    # Fallback: compact "x (lo-hi)" with no explicit "95% CI" marker.
    m = _HR_RE.search(s)
    if not m:
        return None
    hr, lo, hi = (float(m.group(i)) for i in (1, 2, 3))
    if not (0 < lo <= hr <= hi) or hi > 50:
        return None
    return hr, lo, hi


def _paired_values(vmap: dict, pair: dict[str, str], keys: list[str]) -> tuple[dict[str, str], bool]:
    values = {}
    present = False
    for key in keys:
        field = pair.get(key, "")
        v = vmap.get(field)
        if v and _real(v):
            present = True
            values[key] = v.value
        else:
            values[key] = ""
    return values, present


def _raw_value(vmap: dict, field: str) -> str:
    v = vmap.get(field)
    return v.value if (v and _real(v)) else ""


def _arm_mean_sd(vmap: dict, pair: dict[str, str], arm: str, n: int):
    mean = parse_number(_raw_value(vmap, pair.get(f"mean_{arm}", "")))
    sd = parse_number(_raw_value(vmap, pair.get(f"sd_{arm}", "")))
    if mean is not None and sd is not None and sd >= 0:
        return mean, sd, False, ""

    median = parse_number(_raw_value(vmap, pair.get(f"median_{arm}", "")))
    q1 = parse_number(_raw_value(vmap, pair.get(f"q1_{arm}", "")))
    q3 = parse_number(_raw_value(vmap, pair.get(f"q3_{arm}", "")))
    if median is not None and q1 is not None and q3 is not None:
        imputed = mean_sd_from_median_iqr(median, q1, q3, n)
        if imputed is not None:
            m, s, label = imputed
            return m, s, True, label

    lo = parse_number(_raw_value(vmap, pair.get(f"lo_{arm}", "")))
    hi = parse_number(_raw_value(vmap, pair.get(f"hi_{arm}", "")))
    if median is not None and lo is not None and hi is not None:
        imputed = mean_sd_from_median_range(median, lo, hi, n)
        if imputed is not None:
            m, s, label = imputed
            return m, s, True, label
    return None


def _subgroup_for(es: ExtractedStudy, field: str, modality_by_pmid: dict[str, str],
                  subgroup_map: dict[str, list[str]] | None) -> str:
    """The canonical subgroup label for a study: the extracted subgroup field
    (default ``modality``), else the screening map, normalized via the vertical's
    subgroup_map."""
    raw = ""
    if field:
        raw = next((v.value for v in es.values if v.field == field and _real(v)), "")
    raw = raw or es.modality or modality_by_pmid.get(es.pmid, "")
    return canonical_subgroup(raw, subgroup_map)


def _study_value(es: ExtractedStudy, field: str) -> str:
    return next((v.value for v in es.values if v.field == field and _real(v)), "")


# trial_id values that mean "unknown", not a real trial identity — must NOT be used as a
# canonical merge key (they would over-merge distinct studies). Checked raw and normalized.
_PLACEHOLDER_IDS = {"", "nr", "na", "n/a", "n.a", "n.a.", "none", "null", "unknown", "tbd", "?",
                    "not reported", "not applicable", "not available", "not stated",
                    "unspecified", "no id", "no nct"}
# Normalized (alnum-only) placeholders — catches spelled-out forms after stripping punctuation.
_PLACEHOLDER_NORM = {"nr", "na", "none", "null", "unknown", "tbd", "notreported",
                     "notapplicable", "notavailable", "notstated", "unspecified", "noid", "nonct"}


def _trial_key(es: ExtractedStudy) -> str:
    # Prefer the canonical trial_id (set by trial-dedup) so stats curation groups trials
    # identically; fall back to normalized study_name, then the pmid. A placeholder
    # trial_id ("NR", "N/A", ...) is NOT canonical — skip it so it never over-merges trials.
    tid = (getattr(es, "trial_id", "") or "").strip().lower()
    if tid and tid not in _PLACEHOLDER_IDS:
        norm = re.sub(r"[^a-z0-9]+", "", tid)
        if norm and norm not in _PLACEHOLDER_NORM:
            return "tid:" + norm
    name = (es.study_name or "").strip().lower()
    if name and name != "nr":
        return re.sub(r"[^a-z0-9]+", "", name)
    return "pmid:" + str(es.pmid)


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_MONTH_NAME_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)")


def _data_cut_key(es: ExtractedStudy) -> tuple:
    """Sortable (year, month, day) for a data-cut string, so two cuts in the same year
    resolve by month/day (latest wins) instead of first-seen.

    Anchored on the LAST occurrence of the latest year, then read that year's immediate
    neighbours for the month/day. This is robust against: a year being mistaken for the
    day ("Nov 2026"), day-before-month ("15 Nov 2026"), numeric forms ("05/2025",
    "2025-05"), and ranges where the later cut must win — same-year ("Jan 2026 - Nov 2026")
    and ISO ("2024-10 to 2025-05", "02/2022-05/2025")."""
    s = (es.data_cut or "").strip().lower()
    matches = list(_YEAR_RE.finditer(s))
    if not matches:
        return (-1, 0, 0)
    year = max(int(m.group()) for m in matches)
    # Last occurrence of the latest year -> the later half of any range supplies month/day.
    last = max((m for m in matches if int(m.group()) == year), key=lambda m: m.start())
    before, after = s[max(0, last.start() - 12):last.start()], s[last.end():last.end() + 12]
    month = day = 0
    mm = _MONTH_NAME_RE.search(before + " " + after)
    if mm:
        month = _MONTHS[mm.group(1)]
        # A day only exists alongside a named month; a 1-2 digit number in the neighbours
        # (years already excluded from the window) is the day.
        dm = re.search(r"\b([0-3]?\d)\b", before + " " + after)
        if dm and 1 <= int(dm.group(1)) <= 31:
            day = int(dm.group(1))
    else:
        # Numeric month immediately adjacent to the chosen year: "05/2025" or "2025-05".
        mb = re.search(r"(0?[1-9]|1[0-2])\s*[/.\-]\s*$", before)
        ma = re.search(r"^\s*[/.\-]\s*(0?[1-9]|1[0-2])\b", after)
        if mb:
            month = int(mb.group(1))
        elif ma:
            month = int(ma.group(1))
    return (year, month, day)


def _has_field(es: ExtractedStudy, field: str) -> bool:
    return any(v.field == field and _real(v) for v in es.values)


def hr_poolable(es: ExtractedStudy, field: str) -> bool:
    """True iff this study's ``field`` yields a usable HR row (parseable HR+CI, se>0)."""
    v = next((x for x in es.values if x.field == field and _real(x)), None)
    if not v:
        return False
    parsed = parse_hr_ci(v.value)
    if parsed is None:
        return False
    _hr, lo, hi = parsed
    try:
        return (math.log(hi) - math.log(lo)) / (2 * 1.96) > 0
    except (ValueError, ZeroDivisionError):
        return False


def proportion_poolable(es: ExtractedStudy, field: str) -> bool:
    """True iff this study's ``field`` yields a usable proportion row (pct + total N)."""
    vmap = {x.field: x for x in es.values}
    pv, nv = vmap.get(field), vmap.get("total_n")
    if not (pv and _real(pv)):
        return False
    parsed = parse_proportion(pv.value)
    pct = None if parsed is None else parsed.pct
    n = parsed.n if parsed and parsed.n is not None else (
        parse_int(nv.value) if (nv and _real(nv)) else None)
    return pct is not None and bool(n) and n > 0


def curate_for_pool(
    extractions: list[ExtractedStudy], field: str, anchor_terms: list[str] | None = None,
    is_poolable=None,
) -> tuple[list[ExtractedStudy], list[dict]]:
    """Curate extractions for ONE outcome ``field`` before pooling, so a pooled
    estimate is trustworthy. Per-field by design: only records that actually report
    ``field`` are considered, so dedup never deletes a valid estimate just because a
    newer companion report omitted this particular outcome.

    1. Consider only records with a real value for ``field``.
    2. (anchored pooling only — when ``anchor_terms`` given) FAIL CLOSED on the
       comparator: keep a row only if its ``comparator_label`` is present AND matches
       the shared reference comparator; a missing/NR/non-matching comparator is dropped
       (an unverified comparator must not enter an anchored pool).
    3. Collapse multiple data-cuts / companion reports of the SAME trial (that report
       this field) to the most-recent cut — one estimate per trial, no double-counting.

    Returns ``(kept, dropped)`` where ``dropped`` is ``[{pmid, field, reason}, ...]``.
    """
    dropped: list[dict] = []
    terms = [t.lower() for t in (anchor_terms or [])]
    # Only POOLABLE estimates are eligible for dedup, so a newer but unparseable cut
    # cannot evict an older parseable one (which the row parser would then exclude,
    # losing the trial entirely). Defaults to "has a real value" for direct callers.
    poolable = is_poolable or (lambda es: _has_field(es, field))
    have: list[ExtractedStudy] = []
    for es in extractions:
        if not poolable(es):
            continue  # not poolable for this outcome — irrelevant to dedup
        if terms:
            comp = _study_value(es, "comparator_label").lower()
            if not (comp and any(t in comp for t in terms)):
                reason = ("comparator missing/NR (anchor unverifiable)" if not comp
                          else f"comparator not the anchor: {comp[:50]}")
                dropped.append({"pmid": es.pmid, "field": field, "reason": reason})
                continue
        have.append(es)
    best: dict[str, ExtractedStudy] = {}
    for es in have:
        k = _trial_key(es)
        if k not in best or _data_cut_key(es) > _data_cut_key(best[k]):
            best[k] = es
    keep_ids = {id(es) for es in best.values()}
    kept: list[ExtractedStudy] = []
    for es in have:
        if id(es) in keep_ids:
            kept.append(es)
        else:
            dropped.append({"pmid": es.pmid, "field": field,
                            "reason": f"superseded data-cut of trial '{_trial_key(es)}'"})
    return kept, dropped


def hr_rows(
    extractions: list[ExtractedStudy], field: str, modality_by_pmid: dict[str, str],
    *, subgroup_field: str = "", subgroup_map: dict[str, list[str]] | None = None,
) -> tuple[list[HRRow], int]:
    """Rows for inverse-variance HR pooling: logHR + variance from HR + 95% CI."""
    rows: list[HRRow] = []
    excluded = 0
    for es in extractions:
        v = next((x for x in es.values if x.field == field), None)
        if not (v and _real(v)):
            continue
        parsed = parse_hr_ci(v.value)
        if parsed is None:
            excluded += 1
            continue
        hr, lo, hi = parsed
        yi = math.log(hr)
        se = (math.log(hi) - math.log(lo)) / (2 * 1.96)
        if se <= 0:
            excluded += 1
            continue
        sub = _subgroup_for(es, subgroup_field, modality_by_pmid, subgroup_map)
        rows.append(HRRow(es.pmid, sub, hr, yi, se * se))
    return rows, excluded


def outcome_rows(
    extractions: list[ExtractedStudy], field: str, modality_by_pmid: dict[str, str],
    *, subgroup_field: str = "", subgroup_map: dict[str, list[str]] | None = None,
) -> tuple[list[PoolRow], int]:
    """Return (poolable rows, n_excluded) for one outcome field."""
    rows: list[PoolRow] = []
    excluded = 0
    for es in extractions:
        vmap = {v.field: v for v in es.values}
        pv, nv = vmap.get(field), vmap.get("total_n")
        if not (pv and _real(pv)):
            continue  # outcome not reported -> not "excluded", just absent
        parsed = parse_proportion(pv.value)
        pct = None if parsed is None else parsed.pct
        n = parsed.n if parsed and parsed.n is not None else (
            parse_int(nv.value) if (nv and _real(nv)) else None)
        if pct is None or not n or n <= 0:
            excluded += 1
            continue
        events = parsed.events if parsed and parsed.events is not None else round(pct / 100.0 * n)
        sub = _subgroup_for(es, subgroup_field, modality_by_pmid, subgroup_map)
        rows.append(PoolRow(es.pmid, sub, events, n, pct))
    return rows, excluded


def bin_rows(
    extractions: list[ExtractedStudy], pair: dict[str, str], modality_by_pmid: dict[str, str],
    *, subgroup_field: str = "", subgroup_map: dict[str, list[str]] | None = None,
) -> tuple[list[BinRow], int]:
    """Rows for 2x2 binary pooling.

    A study is absent if it reports none of the configured cells. If it reports
    any configured cell but the full 2x2 table is not clean, it is excluded.
    """
    rows: list[BinRow] = []
    excluded = 0
    keys = ["events_tx", "n_tx", "events_ctrl", "n_ctrl"]
    for es in extractions:
        vmap = {v.field: v for v in es.values}
        raw, present = _paired_values(vmap, pair, keys)
        if not present:
            continue
        vals = {k: parse_int(raw[k]) for k in keys}
        if any(vals[k] is None for k in keys):
            excluded += 1
            continue
        events_tx, n_tx = vals["events_tx"], vals["n_tx"]
        events_ctrl, n_ctrl = vals["events_ctrl"], vals["n_ctrl"]
        if not (0 <= events_tx <= n_tx and 0 <= events_ctrl <= n_ctrl and n_tx > 0 and n_ctrl > 0):
            excluded += 1
            continue
        sub = _subgroup_for(es, subgroup_field, modality_by_pmid, subgroup_map)
        rows.append(BinRow(es.pmid, sub, events_tx, n_tx, events_ctrl, n_ctrl))
    return rows, excluded


def cont_rows(
    extractions: list[ExtractedStudy], pair: dict[str, str], modality_by_pmid: dict[str, str],
    *, subgroup_field: str = "", subgroup_map: dict[str, list[str]] | None = None,
) -> tuple[list[ContRow], int]:
    """Rows for continuous pooling (two-arm mean/SD/N per study)."""
    rows: list[ContRow] = []
    excluded = 0
    keys = ["n_tx", "mean_tx", "sd_tx", "n_ctrl", "mean_ctrl", "sd_ctrl",
            "median_tx", "q1_tx", "q3_tx", "lo_tx", "hi_tx",
            "median_ctrl", "q1_ctrl", "q3_ctrl", "lo_ctrl", "hi_ctrl"]
    for es in extractions:
        vmap = {v.field: v for v in es.values}
        raw, present = _paired_values(vmap, pair, keys)
        if not present:
            continue
        n_tx = parse_int(raw["n_tx"])
        n_ctrl = parse_int(raw["n_ctrl"])
        if n_tx is None or n_ctrl is None:
            excluded += 1
            continue
        if n_tx <= 1 or n_ctrl <= 1:
            excluded += 1
            continue
        tx = _arm_mean_sd(vmap, pair, "tx", n_tx)
        ctrl = _arm_mean_sd(vmap, pair, "ctrl", n_ctrl)
        if tx is None or ctrl is None:
            excluded += 1
            continue
        mean_tx, sd_tx, tx_imputed, tx_method = tx
        mean_ctrl, sd_ctrl, ctrl_imputed, ctrl_method = ctrl
        sub = _subgroup_for(es, subgroup_field, modality_by_pmid, subgroup_map)
        methods = sorted({m for m in (tx_method, ctrl_method) if m})
        rows.append(ContRow(
            es.pmid, sub, n_tx, mean_tx, sd_tx, n_ctrl, mean_ctrl, sd_ctrl,
            imputed=tx_imputed or ctrl_imputed,
            imputation_method="; ".join(methods),
        ))
    return rows, excluded


def hedges_g_effect(row: ContRow) -> tuple[float, float] | None:
    """Hedges' g and standard SMD variance for one two-arm continuous study."""
    df = row.n_tx + row.n_ctrl - 2
    if df <= 0 or row.sd_tx < 0 or row.sd_ctrl < 0:
        return None
    pooled_var = ((row.n_tx - 1) * row.sd_tx ** 2 + (row.n_ctrl - 1) * row.sd_ctrl ** 2) / df
    if pooled_var <= 0:
        return None
    pooled_sd = math.sqrt(pooled_var)
    correction = 1 - 3 / (4 * df - 1)
    yi = correction * ((row.mean_tx - row.mean_ctrl) / pooled_sd)
    vi = ((row.n_tx + row.n_ctrl) / (row.n_tx * row.n_ctrl)) + (yi ** 2 / (2 * (row.n_tx + row.n_ctrl)))
    if not (math.isfinite(yi) and math.isfinite(vi)) or vi <= 0:
        return None
    return yi, vi


def metareg_rows(
    extractions: list[ExtractedStudy],
    outcome_field: str,
    moderator_field: str,
    modality_by_pmid: dict[str, str],
    *,
    outcome_kind: str = "log_effect",
    continuous_pair: dict[str, str] | None = None,
    subgroup_field: str = "",
    subgroup_map: dict[str, list[str]] | None = None,
) -> tuple[list[MetaRegRow], int]:
    """Rows for metafor meta-regression with a numeric study-level moderator."""
    rows: list[MetaRegRow] = []
    excluded = 0
    if outcome_kind == "continuous":
        if continuous_pair is None:
            return [], len(extractions)
        cont, excluded = cont_rows(
            extractions, continuous_pair, modality_by_pmid,
            subgroup_field=subgroup_field, subgroup_map=subgroup_map)
        by_pmid = {es.pmid: es for es in extractions}
        for row in cont:
            es = by_pmid.get(row.pmid)
            v = next((x for x in es.values if x.field == moderator_field), None) if es else None
            moderator = parse_number(v.value) if (v and _real(v)) else None
            effect = hedges_g_effect(row)
            if moderator is None or effect is None:
                excluded += 1
                continue
            yi, vi = effect
            rows.append(MetaRegRow(row.pmid, yi, vi, moderator))
        return rows, excluded

    for es in extractions:
        vmap = {v.field: v for v in es.values}
        outcome = vmap.get(outcome_field)
        if not (outcome and _real(outcome)):
            continue
        parsed = parse_hr_ci(outcome.value)
        moderator = parse_number(_raw_value(vmap, moderator_field))
        if parsed is None or moderator is None:
            excluded += 1
            continue
        est, lo, hi = parsed
        yi = math.log(est)
        se = (math.log(hi) - math.log(lo)) / (2 * 1.96)
        vi = se * se
        if se <= 0 or not (math.isfinite(yi) and math.isfinite(vi)):
            excluded += 1
            continue
        rows.append(MetaRegRow(es.pmid, yi, vi, moderator))
    return rows, excluded
