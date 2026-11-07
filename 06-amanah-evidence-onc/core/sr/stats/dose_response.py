"""Dose-response meta-analysis (dosresmeta + restricted cubic splines), honesty-gated.

Models how a quantitative dose/exposure relates to an outcome across studies. PURELY
ADDITIVE and HONESTY-GATED: if the dosresmeta module is absent OR no per-dose-level data
exists, it reports "not performed — <reason>" and fabricates NO curve. Abstract-level
extraction has no dose tables, so the default state is an honest "no per-dose data" —
the harness runs only when real dose-level data is supplied (see README). Mirrors the
NMA feasibility gate (nma.py).
"""

from __future__ import annotations

import csv
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_R_SCRIPT = Path(__file__).resolve().parent / "dosresmeta.R"
_MIN_STUDIES = 3
_MIN_LEVELS = 3   # restricted cubic splines need >= 3 distinct dose points


@dataclass
class DoseLevel:
    dose: float
    events: int
    n: int


@dataclass
class DoseStudy:
    study: str
    modality: str
    dose_unit: str
    levels: list[DoseLevel]


@dataclass
class DoseResult:
    feasible: bool
    reason: str = ""
    outcome: str = ""
    dose_unit: str = ""
    curve: list[dict] = field(default_factory=list)   # [{dose, est, ci_lb, ci_ub}]
    p_nonlinearity: float | None = None
    plot_png: str = ""


def dose_response_available() -> bool:
    from core.sr.stats.meta import _has_rscript, rscript_bin
    if not _has_rscript():
        return False
    try:
        out = subprocess.run(
            [rscript_bin(), "-e", "cat(requireNamespace('dosresmeta', quietly=TRUE) && "
                                  "requireNamespace('rms', quietly=TRUE))"],
            capture_output=True, text=True, timeout=30)
        return "TRUE" in out.stdout
    except Exception:
        return False


def build_dose_data(extractions, modality_by_pmid: dict[str, str],
                    dose_table: dict | None = None) -> list[DoseStudy]:
    """Build per-study dose-level data.

    Honest by design: without a `dose_table` (study pmid -> {unit, levels:[{dose,events,n}]}),
    returns [] — abstract-level extraction carries no dose tiers, and we never invent them
    from a single pooled rate. Supply dose-level extraction to enable.
    """
    if not dose_table:
        return []
    ext_mod = {es.pmid: es.modality for es in extractions}
    out: list[DoseStudy] = []
    for pmid, tab in dose_table.items():
        levels = [DoseLevel(float(l["dose"]), int(l["events"]), int(l["n"]))
                  for l in (tab.get("levels") or [])
                  if l.get("dose") is not None and l.get("n")]
        if not levels:
            continue
        out.append(DoseStudy(study=pmid, modality=ext_mod.get(pmid) or modality_by_pmid.get(pmid, ""),
                             dose_unit=str(tab.get("unit", "")), levels=levels))
    return out


def assess_dose_feasibility(studies: list[DoseStudy], min_studies: int = _MIN_STUDIES,
                            min_levels: int = _MIN_LEVELS) -> tuple[bool, str, str]:
    if not studies:
        return False, "No per-dose-level data extracted (abstract-level run); none fabricated.", ""
    by_unit: dict[str, int] = {}
    for s in studies:
        if len({lv.dose for lv in s.levels}) >= min_levels:
            by_unit[s.dose_unit] = by_unit.get(s.dose_unit, 0) + 1
    if not by_unit:
        return False, f"No study reports the outcome at >= {min_levels} distinct dose levels.", ""
    unit, k = max(by_unit.items(), key=lambda x: x[1])
    if k < min_studies:
        return False, (f"Only {k} study(ies) with >= {min_levels} dose levels in a common unit "
                       f"('{unit}'); need >= {min_studies}."), unit
    return True, "feasible", unit


def run_dose_response(studies: list[DoseStudy], outcome: str, fig_dir: Path) -> DoseResult:
    ok, reason, unit = assess_dose_feasibility(studies)
    if not ok:
        return DoseResult(False, reason, outcome)
    if not dose_response_available():
        return DoseResult(False, "dosresmeta/rms module not installed (set SR_RSCRIPT to a CRAN R "
                          "with dosresmeta + rms); no curve fabricated.", outcome)
    rows = [(s.study, lv.dose, lv.events, lv.n) for s in studies
            if s.dose_unit == unit for lv in s.levels]
    png = fig_dir / f"doseresponse_{outcome}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        incsv, outcsv = Path(tmp) / "dose.csv", Path(tmp) / "curve.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["study", "dose", "cases", "n"])
            for r in rows:
                w.writerow(r)
        try:
            from core.sr.stats.meta import rscript_bin
            res = subprocess.run([rscript_bin(), str(_R_SCRIPT), str(incsv), str(outcsv),
                                  str(png), outcome], check=True, capture_output=True, timeout=300)
            curve, pnl = [], None
            with outcsv.open() as f:
                for d in csv.DictReader(f):
                    if d.get("dose") == "PNL":
                        val = float(d["est"]); pnl = None if val < 0 else val; continue
                    curve.append({"dose": float(d["dose"]), "est": float(d["est"]),
                                  "ci_lb": float(d["ci_lb"]), "ci_ub": float(d["ci_ub"])})
            return DoseResult(True, "ok", outcome, unit, curve, pnl,
                              png.name if png.exists() else "")
        except Exception as exc:
            return DoseResult(False, f"dosresmeta fit failed: {exc}", outcome)
