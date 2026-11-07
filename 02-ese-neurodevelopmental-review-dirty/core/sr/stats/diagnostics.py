"""Sensitivity + publication-bias diagnostics for a pooled effect (Rscript wrapper).

Leave-one-out (k>=3) and Egger/Begg/trim-and-fill (k>=10, honestly gated below that).
Additive and feasibility-gated like the NMA / dose-response stages: returns a result
object whose `.note` states exactly what was and was not performed — never fabricated.
"""

from __future__ import annotations

import csv
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from core.sr.stats.meta import _R_HR_SCRIPT  # noqa: F401  (anchors the package dir)
from core.sr.stats.meta import rscript_bin
from core.sr.stats.prepare import HRRow

_R_DIAG = Path(__file__).resolve().parent / "diagnostics.R"


@dataclass
class LeaveOneOut:
    omitted: str
    hr: float
    ci_lb: float
    ci_ub: float
    i2: float


@dataclass
class EffectDiagnostics:
    field: str
    label: str
    k: int
    leave_one_out: list[LeaveOneOut] = field(default_factory=list)
    egger_p: float | None = None
    begg_p: float | None = None
    trimfill_hr: float | None = None
    trimfill_lb: float | None = None
    trimfill_ub: float | None = None
    n_imputed: int | None = None
    pubbias_note: str = ""
    funnel_png: str = ""


def _f(s):
    s = (s or "").strip()
    if s in ("", "NA"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def run_effect_diagnostics(
    rows: list[HRRow], fieldname: str, label: str, fig_dir: Path
) -> EffectDiagnostics | None:
    """Run leave-one-out + publication-bias diagnostics on a pooled log-effect set."""
    if len(rows) < 3:
        return EffectDiagnostics(fieldname, label, len(rows),
                                 pubbias_note=f"not performed: k={len(rows)} (<3)")
    fig_dir.mkdir(parents=True, exist_ok=True)
    funnel = fig_dir / f"funnel_{fieldname}.png"
    with tempfile.TemporaryDirectory() as tmp:
        incsv = Path(tmp) / "in.csv"
        loocsv, pbcsv = Path(tmp) / "loo.csv", Path(tmp) / "pb.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "yi", "vi"])
            for r in rows:
                w.writerow([r.pmid, r.yi, r.vi])
        try:
            subprocess.run([rscript_bin(), str(_R_DIAG), str(incsv), str(loocsv),
                            str(pbcsv), str(funnel), label],
                           check=True, capture_output=True, timeout=180)
        except Exception as exc:  # never sink the run
            return EffectDiagnostics(fieldname, label, len(rows),
                                     pubbias_note=f"diagnostics failed: {exc}")
        loo: list[LeaveOneOut] = []
        if loocsv.exists():
            with loocsv.open() as f:
                for d in csv.DictReader(f):
                    loo.append(LeaveOneOut(d["omitted"], _f(d["hr"]) or 0.0,
                                           _f(d["ci_lb"]) or 0.0, _f(d["ci_ub"]) or 0.0,
                                           _f(d.get("I2")) or 0.0))
        diag = EffectDiagnostics(fieldname, label, len(rows), leave_one_out=loo,
                                 funnel_png=funnel.name if funnel.exists() else "")
        if pbcsv.exists():
            with pbcsv.open() as f:
                for d in csv.DictReader(f):
                    diag.egger_p = _f(d.get("egger_p"))
                    diag.begg_p = _f(d.get("begg_p"))
                    diag.trimfill_hr = _f(d.get("trimfill_hr"))
                    diag.trimfill_lb = _f(d.get("trimfill_lb"))
                    diag.trimfill_ub = _f(d.get("trimfill_ub"))
                    ni = _f(d.get("n_imputed"))
                    diag.n_imputed = int(ni) if ni is not None else None
                    diag.pubbias_note = d.get("note", "")
        return diag
