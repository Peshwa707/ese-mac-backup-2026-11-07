"""Calibrate the screening operating point against a labeled gold set.

Replicate-and-improve of otto-SR/srma's ``ensemble_shift.py``: srma sweeps a vote
threshold and plots ROC/sensitivity/specificity for a human to eyeball. Here the sweep
auto-selects the operating point that GUARANTEES a target recall (default 0.99 — in a
systematic review, missing an includable study is far costlier than a false include)
and reports the specificity that choice costs, plus AUC and the Youden point.

The include-score per record is the monotone quantity the threshold sweeps:
  * ensemble runs  -> include_votes / ensemble_k   (the srma vote-fraction axis)
  * single pass    -> INCLUDE: confidence, UNCERTAIN: 0.5, EXCLUDE: 1 - confidence
Because the score is monotone, the cutoff admits records strictly in score order: a
high-confidence EXCLUDE scores LOW and is admitted LAST (only under an aggressive,
high-recall cutoff), never ahead of a more-includable record. Pure functions, no
plotting dependency.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from benchmark.models import CalibrationPoint, CalibrationResult
# include_score lives in core so the screening engine can apply the same operating
# point without a core->benchmark dependency; re-exported here for callers/tests.
from core.sr.screen import _rget as _get, include_score

__all__ = ["include_score", "calibrate_screening", "calibrate_run"]


def _axis(records: Iterable[Any]) -> str:
    recs = list(records)
    if recs and all((_get(r, "ensemble_k", 1) or 1) > 1 and _get(r, "include_votes", None) is not None
                    for r in recs):
        return "vote_fraction"
    return "decision_confidence"


def _auc(points: list[tuple[float, float]]) -> float:
    """Trapezoidal AUC over ROC points (fpr, tpr), with (0,0)/(1,1) anchors."""
    pts = sorted(set(points) | {(0.0, 0.0), (1.0, 1.0)})
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        area += (x1 - x0) * (y0 + y1) / 2.0
    return round(area, 4)


def calibrate_screening(
    records: Iterable[Any], gold_pmids: Iterable[str], *,
    target_recall: float = 0.99, case_id: str = "",
) -> CalibrationResult:
    """Sweep the include-score threshold and pick the recall-targeted operating point.

    ``records`` is screening output (dicts or ScreeningResult/EnsembleScreeningResult);
    ``gold_pmids`` is the answer key's expected-included set. A record is positive when
    its pmid is in the gold set. The chosen point is the HIGHEST-specificity threshold
    whose sensitivity still meets ``target_recall``; if none does, the max-recall point
    is returned and the shortfall is flagged (never silently)."""
    recs = list(records)
    gold = {str(p).strip() for p in gold_pmids if str(p).strip()}
    scored = [(include_score(r), str(_get(r, "pmid", "")).strip() in gold) for r in recs]
    n_pos = sum(1 for _, y in scored if y)
    n_neg = len(scored) - n_pos

    result = CalibrationResult(
        case_id=case_id, score_axis=_axis(recs), target_recall=target_recall,
        n_positive=n_pos, n_negative=n_neg,
    )
    if n_pos == 0 or n_neg == 0:
        result.caveats.append(
            "Calibration needs both positive and negative labels; "
            f"got {n_pos} positive / {n_neg} negative — no operating point selected.")
        return result

    # "predict include if score >= threshold": sweep every distinct score.
    thresholds = sorted({s for s, _ in scored})
    roc: list[tuple[float, float]] = []
    best_for_recall: tuple[float, float, float] | None = None   # (spec, thr, sens)
    best_youden: tuple[float, float, float] | None = None       # (J, thr, ...)
    for thr in thresholds:
        tp = sum(1 for s, y in scored if y and s >= thr)
        fp = sum(1 for s, y in scored if (not y) and s >= thr)
        sens = tp / n_pos
        spec = (n_neg - fp) / n_neg
        result.curve.append(CalibrationPoint(threshold=round(thr, 6),
                                             sensitivity=round(sens, 4),
                                             specificity=round(spec, 4)))
        roc.append((1.0 - spec, sens))
        if sens >= target_recall:
            if best_for_recall is None or spec > best_for_recall[0]:
                best_for_recall = (spec, thr, sens)
        j = sens + spec - 1.0
        if best_youden is None or j > best_youden[0]:
            best_youden = (j, thr, sens, spec)   # type: ignore[assignment]

    result.auc = _auc(roc)
    if best_youden is not None:
        _, ythr, ysens, yspec = best_youden  # type: ignore[misc]
        result.youden_threshold = round(ythr, 6)
        result.youden_sensitivity = round(ysens, 4)
        result.youden_specificity = round(yspec, 4)

    if best_for_recall is not None:
        spec, thr, sens = best_for_recall
        result.target_met = True
        result.chosen_threshold = round(thr, 6)
        result.achieved_sensitivity = round(sens, 4)
        result.achieved_specificity = round(spec, 4)
    else:
        # No threshold reaches the target: fall back to max recall (lowest threshold).
        thr = thresholds[0]
        tp = sum(1 for s, y in scored if y and s >= thr)
        fp = sum(1 for s, y in scored if (not y) and s >= thr)
        result.chosen_threshold = round(thr, 6)
        result.achieved_sensitivity = round(tp / n_pos, 4)
        result.achieved_specificity = round((n_neg - fp) / n_neg, 4)
        result.caveats.append(
            f"Target recall {target_recall} unreachable; returned the max-recall point "
            f"(sensitivity {result.achieved_sensitivity}).")
    return result


def calibrate_run(run_dir: Path, gold_pmids: Iterable[str], *,
                  target_recall: float = 0.99, case_id: str = "") -> CalibrationResult:
    """Calibrate a run folder's ``screening_merged.json`` against a gold include set.

    Reads the RAW JSON (not the parsed loader) so ensemble vote counts survive."""
    path = Path(run_dir) / "screening_merged.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return calibrate_screening(data, gold_pmids, target_recall=target_recall, case_id=case_id)
