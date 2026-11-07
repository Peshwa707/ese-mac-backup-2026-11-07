"""Compare engine run-folder outputs to an offline benchmark answer key."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmark.models import (
    AnswerKey,
    ConclusionScore,
    ConclusionStance,
    IntegrityMetrics,
    OutcomeScore,
    OutcomeTarget,
    ScoreReport,
    SelectionMetrics,
)
from core.sr.models import ScreenDecision
from core.tools import sr_io

DRAFT_KEY_CAVEAT = (
    "DRAFT ANSWER KEY USED: this score report is for scaffolding only and must not be "
    "reported as sealed benchmark performance."
)


def _as_list(value) -> list:
    return list(value or [])


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _ci(result: Any) -> list[float] | None:
    lb = _get(result, "ci_lb")
    ub = _get(result, "ci_ub")
    if lb is None or ub is None:
        return None
    return [float(lb), float(ub)]


def _metric_value(result: Any, metric: str) -> float | None:
    value = _get(result, metric)
    return None if value is None else float(value)


def _i2(result: Any) -> float | None:
    value = _get(result, "I2")
    return None if value is None else float(value)


def _engine_outcomes(stats) -> dict[tuple[str, str, str], tuple[Any, str, str, str, int]]:
    """Map (family, field, group) -> (result, label, metric, effect_measure, n_excluded)."""
    out: dict[tuple[str, str, str], tuple[Any, str, str, str, int]] = {}
    families = [
        ("proportion", "outcomes", "prop", "proportion"),
        ("log_effect", "hr_outcomes", "hr", "hr"),
        ("binary", "bin_outcomes", "estimate", ""),
        ("continuous", "cont_outcomes", "estimate", ""),
    ]
    for family, attr, metric, default_measure in families:
        for wrapper in _as_list(_get(stats, attr, [])):
            field = str(_get(wrapper, "field", ""))
            label = str(_get(wrapper, "label", field))
            n_excluded = int(_get(wrapper, "n_excluded", 0) or 0)
            for result in _as_list(_get(wrapper, "results", [])):
                group = str(_get(result, "group", "ALL"))
                if not field:
                    continue
                measure = str(_get(result, "measure", default_measure) or default_measure).lower()
                out[(family, field, group)] = (result, label, metric, measure, n_excluded)
    return out


def _stats_n_excluded(stats) -> int:
    total = 0
    for attr in ("outcomes", "hr_outcomes", "bin_outcomes", "cont_outcomes"):
        for wrapper in _as_list(_get(stats, attr, [])):
            total += int(_get(wrapper, "n_excluded", 0) or 0)
    return total


def _load_engine_includes(run_dir: Path) -> set[str]:
    includes = run_dir / "includes.json"
    if includes.exists():
        return {str(p) for p in json.loads(includes.read_text(encoding="utf-8"))}
    screening = sr_io.load_tool_screening(run_dir)
    return {s.pmid for s in screening if s.decision == ScreenDecision.INCLUDE}


def _metric_set(expected: list[str], observed: set[str]) -> dict[str, Any]:
    exp = {str(p) for p in expected if str(p)}
    obs = {str(p) for p in observed if str(p)}
    overlap = exp & obs
    union = exp | obs
    if not exp:
        return {
            "expected_n": 0,
            "engine_n": len(obs),
            "overlap_n": 0,
            "recall": None,
            "precision": None if not obs else 0.0,
            "jaccard": None,
        }
    return {
        "expected_n": len(exp),
        "engine_n": len(obs),
        "overlap_n": len(overlap),
        "recall": len(overlap) / len(exp),
        "precision": len(overlap) / len(obs) if obs else 0.0,
        "jaccard": len(overlap) / len(union) if union else None,
    }


def selection_metrics(
    expected_reports: list[str],
    observed_reports: set[str],
    expected_studies: list[str] | None = None,
    observed_studies: set[str] | None = None,
) -> SelectionMetrics:
    report = _metric_set(expected_reports, observed_reports)
    study = _metric_set(expected_studies or [], observed_studies or set())
    return SelectionMetrics(
        expected_n=report["expected_n"],
        engine_n=report["engine_n"],
        overlap_n=report["overlap_n"],
        recall=report["recall"],
        precision=report["precision"],
        jaccard=report["jaccard"],
        report_expected_n=report["expected_n"],
        report_engine_n=report["engine_n"],
        report_overlap_n=report["overlap_n"],
        report_recall=report["recall"],
        report_precision=report["precision"],
        report_jaccard=report["jaccard"],
        study_expected_n=study["expected_n"],
        study_engine_n=study["engine_n"],
        study_overlap_n=study["overlap_n"],
        study_recall=study["recall"],
        study_precision=study["precision"],
        study_jaccard=study["jaccard"],
    )


def _study_id_for_extraction(extraction: Any) -> str | None:
    trial_id = str(_get(extraction, "trial_id", "") or "").strip()
    if trial_id:
        return trial_id
    member_pmids = sorted(str(p) for p in _as_list(_get(extraction, "member_pmids", [])) if str(p))
    if member_pmids:
        return "pmids:" + ",".join(member_pmids)
    pmid = str(_get(extraction, "pmid", "") or "").strip()
    return pmid or None


def _fold_engine_studies(includes: set[str], extractions: list[Any]) -> set[str]:
    folded: set[str] = set()
    consumed_pmids: set[str] = set()
    for extraction in extractions:
        pmid = str(_get(extraction, "pmid", "") or "").strip()
        members = {str(p) for p in _as_list(_get(extraction, "member_pmids", [])) if str(p)}
        represented = ({pmid} if pmid else set()) | members
        if not represented & includes:
            continue
        study_id = _study_id_for_extraction(extraction)
        if study_id:
            folded.add(study_id)
        consumed_pmids |= represented & includes
    folded |= includes - consumed_pmids
    return folded


def _target_measure(target: OutcomeTarget, engine_measure: str = "") -> str:
    measure = (target.effect_measure or engine_measure or "").lower()
    if target.family == "proportion":
        return "proportion"
    if target.family == "log_effect":
        return measure or "hr"
    if target.family == "binary":
        return measure or "rr"
    if target.family == "continuous":
        return measure or "smd"
    return measure or "estimate"


def _null_value(measure: str) -> float | None:
    measure = measure.lower()
    if measure in {"hr", "rr", "or"}:
        return 1.0
    if measure in {"rd", "smd", "md"}:
        return 0.0
    return None


def _direction(value: float | None, null: float | None) -> str:
    if value is None or null is None:
        return "na"
    if value > null:
        return "above_null"
    if value < null:
        return "below_null"
    return "at_null"


def _significance(ci: list[float] | None, null: float | None) -> str:
    if ci is None or null is None:
        return "na"
    if ci[1] < null or ci[0] > null:
        return "significant"
    return "not_significant"


def _contains(ci: list[float] | None, value: float | None) -> bool | None:
    if ci is None or value is None:
        return None
    return ci[0] <= value <= ci[1]


def _interval_overlap(a: list[float] | None, b: list[float] | None) -> bool | None:
    if a is None or b is None:
        return None
    return max(a[0], b[0]) <= min(a[1], b[1])


def _concordance_fields(
    *,
    effect_measure: str,
    expected: float | None,
    observed: float | None,
    expected_ci: list[float] | None,
    observed_ci: list[float] | None,
    expected_i2: float | None,
    observed_i2: float | None,
) -> dict[str, Any]:
    null = _null_value(effect_measure)
    expected_direction = _direction(expected, null)
    observed_direction = _direction(observed, null)
    expected_significance = _significance(expected_ci, null)
    observed_significance = _significance(observed_ci, null)
    direction_agree = None
    if expected_direction != "na" and observed_direction != "na":
        direction_agree = expected_direction == observed_direction
    significance_agree = None
    if expected_significance != "na" and observed_significance != "na":
        significance_agree = expected_significance == observed_significance
    ci_interval_overlap = _interval_overlap(expected_ci, observed_ci)
    headline_pass = None
    if null is None:
        headline_pass = ci_interval_overlap
    elif direction_agree is not None and significance_agree is not None and ci_interval_overlap is not None:
        headline_pass = direction_agree and significance_agree and ci_interval_overlap
    return {
        "effect_measure": effect_measure,
        "null_value": null,
        "expected_direction": expected_direction,
        "observed_direction": observed_direction,
        "expected_significance": expected_significance,
        "observed_significance": observed_significance,
        "direction_agree": direction_agree,
        "significance_agree": significance_agree,
        "engine_estimate_in_published_ci": _contains(expected_ci, observed),
        "published_estimate_in_engine_ci": _contains(observed_ci, expected),
        "ci_interval_overlap": ci_interval_overlap,
        "expected_i2": expected_i2,
        "observed_i2": observed_i2,
        "i2_delta": None if expected_i2 is None or observed_i2 is None else observed_i2 - expected_i2,
        "headline_pass": headline_pass,
    }


def _score_target(
    target: OutcomeTarget,
    engine: dict[tuple[str, str, str], tuple[Any, str, str, str, int]],
) -> OutcomeScore:
    expected_ci = [target.ci_lb, target.ci_ub] if target.ci_lb is not None else None
    if not target.is_scored:
        return OutcomeScore(
            field=target.field,
            label=target.label,
            family=target.family,
            group=target.group,
            effect_measure=_target_measure(target),
            classification="not_scored",
            expected_ci=expected_ci,
            expected_i2=target.i2,
            note="answer key target is unfilled",
        )
    item = engine.get((target.family, target.field, target.group))
    if item is None:
        return OutcomeScore(
            field=target.field,
            label=target.label,
            family=target.family,
            group=target.group,
            effect_measure=_target_measure(target),
            classification="missing_engine",
            expected=target.estimate,
            expected_ci=expected_ci,
            expected_i2=target.i2,
            note="engine did not produce this outcome/group",
        )
    result, label, metric, engine_measure, _n_excluded = item
    observed = _metric_value(result, metric)
    observed_ci = _ci(result)
    observed_i2 = _i2(result)
    effect_measure = _target_measure(target, engine_measure)
    concordance = _concordance_fields(
        effect_measure=effect_measure,
        expected=target.estimate,
        observed=observed,
        expected_ci=expected_ci,
        observed_ci=observed_ci,
        expected_i2=target.i2,
        observed_i2=observed_i2,
    )
    if observed is None:
        classification = "missing_engine"
        note = f"engine result missing metric {metric}"
    elif concordance["headline_pass"] is True:
        classification = "matched"
        note = ""
    elif concordance["headline_pass"] is False:
        classification = "mismatched"
        note = "direction/significance/CI-overlap concordance failed"
    else:
        classification = "not_scored"
        note = "insufficient CI/null information for concordance"
    return OutcomeScore(
        field=target.field,
        label=target.label or label,
        family=target.family,
        group=target.group,
        classification=classification,
        expected=target.estimate,
        observed=observed,
        delta=None if observed is None or target.estimate is None else observed - float(target.estimate),
        expected_ci=expected_ci,
        observed_ci=observed_ci,
        note=note,
        **concordance,
    )


def _score_unexpected_engine_outcome(family: str, field: str, group: str, item) -> OutcomeScore:
    result, label, metric, engine_measure, _n_excluded = item
    observed = _metric_value(result, metric)
    observed_ci = _ci(result)
    observed_i2 = _i2(result)
    effect_measure = engine_measure or ("proportion" if family == "proportion" else "")
    concordance = _concordance_fields(
        effect_measure=effect_measure,
        expected=None,
        observed=observed,
        expected_ci=None,
        observed_ci=observed_ci,
        expected_i2=None,
        observed_i2=observed_i2,
    )
    return OutcomeScore(
        field=field,
        label=label,
        family=family,  # type: ignore[arg-type]
        group=group,
        classification="missing_key",
        observed=observed,
        observed_ci=observed_ci,
        note="engine produced outcome absent from answer key",
        **concordance,
    )


def _integrity_metrics(extractions: list[Any], stats) -> IntegrityMetrics:
    estimated = 0
    nr = 0
    for study in extractions:
        for value in _as_list(_get(study, "values", [])):
            flag = str(_get(value, "flag", "") or "").upper()
            raw = str(_get(value, "value", "") or "").strip()
            if flag == "ESTIMATED" or raw.upper().startswith("ESTIMATED"):
                estimated += 1
            if flag == "NR" or raw.upper() == "NR":
                nr += 1
    return IntegrityMetrics(
        estimated_value_count=estimated,
        nr_value_count=nr,
        pooled_n_excluded_total=_stats_n_excluded(stats),
    )


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_extraction_items(run_dir: Path) -> list[Any]:
    src = run_dir / "extractions_merged.json"
    if not src.exists():
        src = run_dir / "extractions.json"
    if not src.exists():
        return []
    raw = _read_json(src)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("extractions", "studies"):
            if isinstance(raw.get(key), list):
                return raw[key]
    return []


def _text_from_narrative(payload: dict[str, Any]) -> str:
    final = payload.get("final")
    if isinstance(final, dict):
        return str(final.get("abstract_conclusions") or final.get("discussion") or "")
    for key in ("abstract_conclusions", "conclusions", "summary"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _infer_stance(text: str) -> tuple[ConclusionStance | None, str]:
    lower = text.lower()
    if not lower.strip():
        return None, "narrative text unavailable"
    if "withheld" in lower or "conclusions locked" in lower or "awaiting human" in lower:
        return "withheld", "inferred withheld from lock text"
    no_effect_terms = ("no effect", "no significant", "not associated", "did not improve", "did not reduce")
    if any(term in lower for term in no_effect_terms):
        return "no-effect", "inferred from narrative text"
    uncertain_terms = ("uncertain", "insufficient", "limited", "inconclusive", "not complete")
    if any(term in lower for term in uncertain_terms):
        return "uncertain", "inferred from narrative text"
    return "effect", "inferred from narrative text"


def _conclusion_score(run_dir: Path, key: AnswerKey) -> ConclusionScore:
    payload = _read_json(run_dir / "narrative.json")
    if not isinstance(payload, dict):
        return ConclusionScore(expected=key.expected_conclusion, note="narrative.json unavailable")
    observed: ConclusionStance | None
    note = ""
    if bool(payload.get("locked")):
        observed = "withheld"
        note = "narrative is conclusion-locked"
    else:
        explicit = str(payload.get("stance") or payload.get("qualitative_stance") or "").strip()
        if explicit in {"effect", "no-effect", "uncertain", "withheld"}:
            observed = explicit  # type: ignore[assignment]
            note = "read explicit narrative stance"
        else:
            observed, note = _infer_stance(_text_from_narrative(payload))
    matched = None if key.expected_conclusion is None or observed is None else key.expected_conclusion == observed
    return ConclusionScore(expected=key.expected_conclusion, observed=observed, matched=matched, note=note)


def score_run(
    run_dir: Path,
    answer_key_path: Path,
    out_path: Path,
    *,
    allow_draft: bool = False,
) -> ScoreReport:
    key = AnswerKey.model_validate_json(answer_key_path.read_text(encoding="utf-8"))
    if key.status != "sealed" and not allow_draft:
        raise ValueError("answer key is draft; pass --allow-draft for scaffold-only scoring")
    caveats = list(key.caveats)
    if key.status != "sealed":
        caveats.insert(0, DRAFT_KEY_CAVEAT)

    stats = sr_io.load_stats(run_dir)
    if stats is None:
        stats = SimpleNamespace(available=False, outcomes=[], hr_outcomes=[], bin_outcomes=[], cont_outcomes=[])
        caveats.append("No stats artifact was available.")
    if not bool(_get(stats, "available", False)):
        note = str(_get(stats, "note", "") or "stats unavailable")
        caveats.append(note)

    # Exercise the shared loaders so scoring follows the run-folder dialect.
    if (run_dir / "screening_merged.json").exists():
        sr_io.load_tool_screening(run_dir)
    extractions: list[Any] = []
    raw_extractions = _raw_extraction_items(run_dir)
    if (run_dir / "extractions.json").exists() or (run_dir / "extractions_merged.json").exists():
        extractions = sr_io.load_tool_extractions(run_dir)
    sr_io.modality_by_pmid(run_dir)
    if (run_dir / "corpus.json").exists():
        try:
            sr_io.load_run_bundle(run_dir)
        except Exception as exc:
            caveats.append(f"load_run_bundle unavailable for scoring context: {exc}")

    observed_includes = _load_engine_includes(run_dir) if (
        (run_dir / "includes.json").exists() or (run_dir / "screening_merged.json").exists()
    ) else set()
    observed_studies = _fold_engine_studies(observed_includes, extractions)
    engine = _engine_outcomes(stats)
    scores = [_score_target(target, engine) for target in key.outcomes]

    key_set = {(o.family, o.field, o.group) for o in key.outcomes}
    for (family, field, group), item in sorted(engine.items()):
        if (family, field, group) not in key_set:
            scores.append(_score_unexpected_engine_outcome(family, field, group, item))

    integrity = _integrity_metrics(raw_extractions, stats)
    if integrity.estimated_value_count:
        caveats.append(
            f"ESTIMATED extracted values present ({integrity.estimated_value_count}); "
            "benchmark fabrication count must be 0 for sealed claims."
        )

    report = ScoreReport(
        case_id=key.case_id,
        key_sealed=key.status == "sealed",
        selection=selection_metrics(
            key.expected_included_pmids,
            observed_includes,
            key.expected_included_trial_ids,
            observed_studies,
        ),
        integrity=integrity,
        conclusion=_conclusion_score(run_dir, key),
        outcomes=scores,
        caveats=[c for c in caveats if c],
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
