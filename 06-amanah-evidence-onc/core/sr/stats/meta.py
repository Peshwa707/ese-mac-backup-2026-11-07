"""Run the R meta-analysis via Rscript subprocess and parse results."""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.sr.stats.prepare import BinRow, ContRow, HRRow, MetaRegRow, PoolRow

_R_SCRIPT = Path(__file__).resolve().parent / "metaprop.R"
_R_HR_SCRIPT = Path(__file__).resolve().parent / "metahr.R"
_R_BIN_SCRIPT = Path(__file__).resolve().parent / "metabin.R"
_R_CONT_SCRIPT = Path(__file__).resolve().parent / "metacont.R"
_R_METAREG_SCRIPT = Path(__file__).resolve().parent / "metareg.R"


def rscript_bin() -> str:
    """The Rscript to use — SR_RSCRIPT env override, else 'Rscript' on PATH.

    Lets you point the engine at the official CRAN R (which has working binary
    packages for metafor/gemtc) without fighting PATH against a Homebrew R.
    """
    return os.environ.get("SR_RSCRIPT", "Rscript")


def _has_rscript() -> bool:
    binp = rscript_bin()
    return bool(shutil.which(binp) or os.path.exists(binp))


def r_available() -> bool:
    return _r_package_available("metafor")


def _r_package_available(package: str) -> bool:
    if not _has_rscript():
        return False
    try:
        out = subprocess.run(
            [rscript_bin(), "-e", f"cat(requireNamespace('{package}', quietly=TRUE))"],
            capture_output=True, text=True, timeout=30,
        )
        return "TRUE" in out.stdout
    except Exception:
        return False


def metabin_available() -> bool:
    return _r_package_available("meta")


def metacont_available() -> bool:
    return _r_package_available("meta")


def metareg_available() -> bool:
    return _r_package_available("metafor")


def _float_or_none(value) -> float | None:
    s = str(value if value is not None else "").strip()
    if s == "" or s.upper() in {"NA", "NAN", "NULL"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class PoolResult:
    group: str
    k: int
    prop: float      # percent
    ci_lb: float     # percent
    ci_ub: float     # percent
    I2: float
    tau2: float
    Q: float | None = None
    I2_lb: float | None = None
    I2_ub: float | None = None
    pi_lb: float | None = None
    pi_ub: float | None = None


def run_metaprop(rows: list[PoolRow], label: str, png_path: Path) -> list[PoolResult]:
    """Pool one outcome (overall + by modality); writes a forest PNG to png_path."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        incsv = Path(tmp) / "in.csv"
        outcsv = Path(tmp) / "out.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "modality", "events", "n"])
            for r in rows:
                w.writerow([r.pmid, r.modality, r.events, r.n])
        subprocess.run(
            [rscript_bin(), str(_R_SCRIPT), str(incsv), str(outcsv), str(png_path), label],
            check=True, capture_output=True, timeout=180,
        )
        results: list[PoolResult] = []
        with outcsv.open() as f:
            for d in csv.DictReader(f):
                results.append(PoolResult(
                    group=d["group"], k=int(d["k"]),
                    prop=round(float(d["prop"]) * 100, 1),
                    ci_lb=round(float(d["ci_lb"]) * 100, 1),
                    ci_ub=round(float(d["ci_ub"]) * 100, 1),
                    I2=float(d["I2"]), tau2=float(d["tau2"]),
                    Q=_float_or_none(d.get("Q")),
                    I2_lb=_float_or_none(d.get("I2_lb")),
                    I2_ub=_float_or_none(d.get("I2_ub")),
                    pi_lb=_float_or_none(d.get("pi_lb")),
                    pi_ub=_float_or_none(d.get("pi_ub")),
                ))
        return results


@dataclass
class HRPoolResult:
    group: str
    k: int
    hr: float
    ci_lb: float
    ci_ub: float
    I2: float
    Q: float | None = None
    I2_lb: float | None = None
    I2_ub: float | None = None
    tau2: float | None = None
    pi_lb: float | None = None
    pi_ub: float | None = None


def run_metahr(rows: list[HRRow], label: str, png_path: Path) -> list[HRPoolResult]:
    """Inverse-variance random-effects pooling of hazard ratios; writes a forest PNG."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        incsv, outcsv = Path(tmp) / "in.csv", Path(tmp) / "out.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "modality", "yi", "vi"])
            for r in rows:
                w.writerow([r.pmid, r.modality, r.yi, r.vi])
        subprocess.run([rscript_bin(), str(_R_HR_SCRIPT), str(incsv), str(outcsv), str(png_path), label],
                       check=True, capture_output=True, timeout=180)
        out: list[HRPoolResult] = []
        with outcsv.open() as f:
            for d in csv.DictReader(f):
                out.append(HRPoolResult(
                    group=d["group"], k=int(d["k"]), hr=float(d["hr"]),
                    ci_lb=float(d["ci_lb"]), ci_ub=float(d["ci_ub"]), I2=float(d["I2"]),
                    Q=_float_or_none(d.get("Q")),
                    I2_lb=_float_or_none(d.get("I2_lb")),
                    I2_ub=_float_or_none(d.get("I2_ub")),
                    tau2=_float_or_none(d.get("tau2")),
                    pi_lb=_float_or_none(d.get("pi_lb")),
                    pi_ub=_float_or_none(d.get("pi_ub")),
                ))
        return out


@dataclass
class NumberNeeded:
    label: str
    value: float | None
    ci_lb: float | None
    ci_ub: float | None
    direction: str
    note: str = ""


def interpret_number_needed(rd: float | None, rd_lb: float | None, rd_ub: float | None,
                            beneficial: bool | None) -> NumberNeeded:
    if rd is None or rd_lb is None or rd_ub is None:
        return NumberNeeded("not estimable", None, None, None,
                            "risk difference unavailable",
                            "risk difference unavailable")
    if rd_lb <= 0 <= rd_ub:
        return NumberNeeded("not estimable", None, None, None,
                            "risk difference CI crosses zero",
                            "risk difference CI crosses zero")
    if rd == 0:
        return NumberNeeded("not estimable", None, None, None,
                            "risk difference is zero",
                            "risk difference is zero")

    higher_events = rd > 0
    direction = "treatment event rate higher" if higher_events else "treatment event rate lower"
    nn = abs(1.0 / rd)
    ci_vals = sorted([abs(1.0 / rd_lb), abs(1.0 / rd_ub)])

    if beneficial is True:
        label = "NNT" if higher_events else "NNH"
        note = "events configured as beneficial"
    elif beneficial is False:
        label = "NNH" if higher_events else "NNT"
        note = "events configured as harmful"
    else:
        label = "NNC"
        note = "event desirability not configured; interpret as number needed to change one event"
    return NumberNeeded(label, nn, ci_vals[0], ci_vals[1], direction, note)


@dataclass
class BinPoolResult:
    group: str
    k: int
    measure: str
    estimate: float
    ci_lb: float
    ci_ub: float
    rd: float | None
    rd_lb: float | None
    rd_ub: float | None
    number_needed: NumberNeeded
    Q: float | None = None
    I2: float | None = None
    I2_lb: float | None = None
    I2_ub: float | None = None
    tau2: float | None = None
    pi_lb: float | None = None
    pi_ub: float | None = None


@dataclass
class ContPoolResult:
    group: str
    k: int
    measure: str
    estimate: float
    ci_lb: float
    ci_ub: float
    Q: float | None = None
    I2: float | None = None
    I2_lb: float | None = None
    I2_ub: float | None = None
    tau2: float | None = None
    pi_lb: float | None = None
    pi_ub: float | None = None


@dataclass
class MetaRegTerm:
    term: str
    estimate: float
    se: float
    ci_lb: float
    ci_ub: float
    pval: float


@dataclass
class MetaRegResult:
    available: bool
    reliable: bool
    note: str
    k: int
    terms: list[MetaRegTerm]
    QM: float | None = None
    QM_p: float | None = None
    QE: float | None = None
    QE_p: float | None = None
    I2_resid: float | None = None
    R2: float | None = None


def run_metabin(rows: list[BinRow], label: str, png_path: Path, *,
                measure: str = "RR", method: str = "MH",
                beneficial: bool | None = None) -> list[BinPoolResult]:
    """Pool 2x2 binary outcomes with R meta::metabin; writes a forest PNG."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        incsv, outcsv = Path(tmp) / "in.csv", Path(tmp) / "out.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "modality", "events_tx", "n_tx", "events_ctrl", "n_ctrl"])
            for r in rows:
                w.writerow([r.pmid, r.modality, r.events_tx, r.n_tx, r.events_ctrl, r.n_ctrl])
        subprocess.run(
            [rscript_bin(), str(_R_BIN_SCRIPT), str(incsv), str(outcsv), str(png_path),
             label, measure, method],
            check=True, capture_output=True, timeout=180,
        )
        out: list[BinPoolResult] = []
        with outcsv.open() as f:
            for d in csv.DictReader(f):
                rd, rd_lb, rd_ub = (_float_or_none(d.get("rd")),
                                    _float_or_none(d.get("rd_lb")),
                                    _float_or_none(d.get("rd_ub")))
                out.append(BinPoolResult(
                    group=d["group"], k=int(d["k"]),
                    measure=d.get("measure") or measure,
                    estimate=float(d["estimate"]),
                    ci_lb=float(d["ci_lb"]), ci_ub=float(d["ci_ub"]),
                    rd=rd, rd_lb=rd_lb, rd_ub=rd_ub,
                    number_needed=interpret_number_needed(rd, rd_lb, rd_ub, beneficial),
                    Q=_float_or_none(d.get("Q")),
                    I2=_float_or_none(d.get("I2")),
                    I2_lb=_float_or_none(d.get("I2_lb")),
                    I2_ub=_float_or_none(d.get("I2_ub")),
                    tau2=_float_or_none(d.get("tau2")),
                    pi_lb=_float_or_none(d.get("pi_lb")),
                    pi_ub=_float_or_none(d.get("pi_ub")),
                ))
        return out


def run_metareg(rows: list[MetaRegRow], moderator_label: str, *, min_k: int = 10) -> MetaRegResult:
    """Fit metafor meta-regression with an honesty gate for small k."""
    k = len(rows)
    if k < 4:
        return MetaRegResult(False, False, "insufficient studies for meta-regression (k<4)", k, [])
    reliable = k >= min_k
    note = "" if reliable else (
        f"k={k} below the recommended >={min_k} studies per covariate — exploratory only, "
        "do not over-interpret"
    )
    with tempfile.TemporaryDirectory() as tmp:
        incsv, outcsv = Path(tmp) / "in.csv", Path(tmp) / "out.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "yi", "vi", "moderator"])
            for r in rows:
                w.writerow([r.pmid, r.yi, r.vi, r.moderator])
        subprocess.run(
            [rscript_bin(), str(_R_METAREG_SCRIPT), str(incsv), str(outcsv), moderator_label],
            check=True, capture_output=True, timeout=180,
        )
        terms: list[MetaRegTerm] = []
        summary: dict[str, float | None] = {}
        with outcsv.open() as f:
            for d in csv.DictReader(f):
                terms.append(MetaRegTerm(
                    term=d["term"],
                    estimate=float(d["estimate"]),
                    se=float(d["se"]),
                    ci_lb=float(d["ci_lb"]),
                    ci_ub=float(d["ci_ub"]),
                    pval=float(d["pval"]),
                ))
                for key in ("QM", "QM_p", "QE", "QE_p", "I2_resid", "R2"):
                    summary[key] = _float_or_none(d.get(key))
        return MetaRegResult(
            True, reliable, note, k, terms,
            QM=summary.get("QM"),
            QM_p=summary.get("QM_p"),
            QE=summary.get("QE"),
            QE_p=summary.get("QE_p"),
            I2_resid=summary.get("I2_resid"),
            R2=summary.get("R2"),
        )


def run_metacont(rows: list[ContRow], label: str, png_path: Path, *,
                 measure: str = "SMD") -> list[ContPoolResult]:
    """Pool two-arm continuous outcomes with R meta::metacont; writes a forest PNG."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        incsv, outcsv = Path(tmp) / "in.csv", Path(tmp) / "out.csv"
        with incsv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pmid", "modality", "n_tx", "mean_tx", "sd_tx",
                        "n_ctrl", "mean_ctrl", "sd_ctrl"])
            for r in rows:
                w.writerow([r.pmid, r.modality, r.n_tx, r.mean_tx, r.sd_tx,
                            r.n_ctrl, r.mean_ctrl, r.sd_ctrl])
        subprocess.run(
            [rscript_bin(), str(_R_CONT_SCRIPT), str(incsv), str(outcsv), str(png_path),
             label, measure],
            check=True, capture_output=True, timeout=180,
        )
        out: list[ContPoolResult] = []
        with outcsv.open() as f:
            for d in csv.DictReader(f):
                out.append(ContPoolResult(
                    group=d["group"], k=int(d["k"]),
                    measure=d.get("measure") or measure,
                    estimate=float(d["estimate"]),
                    ci_lb=float(d["ci_lb"]), ci_ub=float(d["ci_ub"]),
                    Q=_float_or_none(d.get("Q")),
                    I2=_float_or_none(d.get("I2")),
                    I2_lb=_float_or_none(d.get("I2_lb")),
                    I2_ub=_float_or_none(d.get("I2_ub")),
                    tau2=_float_or_none(d.get("tau2")),
                    pi_lb=_float_or_none(d.get("pi_lb")),
                    pi_ub=_float_or_none(d.get("pi_ub")),
                ))
        return out
