"""Conservative continuous-outcome imputation helpers.

These utilities recover studies that report summaries such as median/IQR or
median/range instead of mean/SD. They return ``None`` for invalid inputs rather
than guessing; callers must surface any derived values as imputed.
"""

from __future__ import annotations

import math
from statistics import NormalDist


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(v)) for v in values)


def sd_from_se(se: float, n: int) -> float | None:
    """Convert standard error to SD using SD = SE * sqrt(n)."""
    if n < 2 or not _finite(se) or se < 0:
        return None
    return float(se) * math.sqrt(n)


def sd_from_ci(lo: float, hi: float, n: int, conf: float = 0.95) -> float | None:
    """Convert a normal-approximation CI for a mean to SD.

    Assumes a normal-approximation CI; for small n (t-based CIs) this slightly
    under-estimates SD — a known, accepted limitation. Formula:
    SD = ((hi - lo) / (2*z_{1-alpha/2})) * sqrt(n).
    """
    if n < 2 or not (0 < conf < 1) or not _finite(lo, hi) or hi < lo:
        return None
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2)
    if z <= 0:
        return None
    return ((float(hi) - float(lo)) / (2 * z)) * math.sqrt(n)


def sd_from_range(lo: float, hi: float, n: int) -> float | None:
    """Estimate SD from range using Hozo et al. 2005 style rules.

    Uses range/sqrt(12) for very small samples, range/4 for moderate samples,
    and range/6 for large samples. Returns ``None`` for invalid ranges.
    """
    if n < 2 or not _finite(lo, hi) or hi < lo:
        return None
    span = float(hi) - float(lo)
    if n <= 15:
        return span / math.sqrt(12)
    if n <= 70:
        return span / 4
    return span / 6


def mean_sd_from_median_iqr(median: float, q1: float, q3: float, n: int) -> tuple[float, float, str] | None:
    """Estimate mean/SD from median and IQR.

    Mean follows Wan et al. 2014's simple median/IQR estimator
    ``(q1 + median + q3) / 3``. SD follows Wan et al. 2014 / Luo et al. 2018
    using ``(q3-q1) / (2*Phi^-1((0.75*n - 0.125)/(n + 0.25)))``.
    """
    if n < 2 or not _finite(median, q1, q3) or not (q1 <= median <= q3):
        return None
    p = (0.75 * n - 0.125) / (n + 0.25)
    if not (0.5 < p < 1):
        return None
    denom = 2 * NormalDist().inv_cdf(p)
    if denom <= 0:
        return None
    mean = (float(q1) + float(median) + float(q3)) / 3
    sd = (float(q3) - float(q1)) / denom
    if sd < 0 or not _finite(mean, sd):
        return None
    return mean, sd, "Wan 2014/Luo 2018 median/IQR"


def mean_sd_from_median_range(median: float, lo: float, hi: float, n: int) -> tuple[float, float, str] | None:
    """Estimate mean/SD from median and range.

    Mean uses Wan et al. 2014 / Hozo et al. 2005 style rules:
    ``(lo + 2*median + hi) / 4`` for n<=25, ``(lo + median + hi) / 3`` for
    25<n<=70, and median for larger samples. SD uses ``sd_from_range``.
    """
    if n < 2 or not _finite(median, lo, hi) or not (lo <= median <= hi):
        return None
    sd = sd_from_range(lo, hi, n)
    if sd is None:
        return None
    if n <= 25:
        mean = (float(lo) + 2 * float(median) + float(hi)) / 4
    elif n <= 70:
        mean = (float(lo) + float(median) + float(hi)) / 3
    else:
        mean = float(median)
    if not _finite(mean, sd):
        return None
    return mean, sd, "Wan 2014/Hozo 2005 median/range"
