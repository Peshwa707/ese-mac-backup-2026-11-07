"""Orchestrate pooled-proportion meta-analysis across outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.sr.models import ExtractedStudy
from core.sr.stats.meta import (
    BinPoolResult,
    ContPoolResult,
    HRPoolResult,
    PoolResult,
    metabin_available,
    metacont_available,
    r_available,
    run_metabin,
    run_metacont,
    run_metahr,
    run_metaprop,
)
from core.sr.stats.prepare import (
    bin_rows,
    cont_rows,
    curate_for_pool,
    hr_poolable,
    hr_rows,
    outcome_rows,
    proportion_poolable,
)


@dataclass
class HROutcome:
    field: str
    label: str
    results: list[HRPoolResult]
    n_pooled: int
    n_excluded: int
    forest_png: str = ""
    diagnostics: object = None      # EffectDiagnostics | None (LOO + publication bias)


@dataclass
class StatsOutcome:
    field: str
    label: str
    results: list[PoolResult]
    n_pooled: int
    n_excluded: int
    forest_png: str = ""   # filename in figures/


@dataclass
class BinOutcome:
    field: str
    label: str
    results: list[BinPoolResult]
    n_pooled: int
    n_excluded: int
    forest_png: str = ""


@dataclass
class ContOutcome:
    field: str
    label: str
    results: list[ContPoolResult]
    n_pooled: int
    n_excluded: int
    forest_png: str = ""


@dataclass
class StatsResult:
    available: bool
    note: str = ""
    outcomes: list[StatsOutcome] = field(default_factory=list)
    hr_outcomes: list[HROutcome] = field(default_factory=list)
    bin_outcomes: list[BinOutcome] = field(default_factory=list)
    cont_outcomes: list[ContOutcome] = field(default_factory=list)
    nma: object = None              # NMAResult | None
    dose_response: object = None    # DoseResult | None
    meta_regression: object = None  # MetaRegResult | None


def _beneficial(value) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "yes", "1"}:
            return True
        if low in {"false", "no", "0"}:
            return False
    return None


def meta_analyze(
    extractions: list[ExtractedStudy], modality_by_pmid: dict[str, str], fig_dir: Path,
    *, cfg=None, dose_table: dict | None = None, dose_outcome: str | None = None,
) -> StatsResult:
    # Poolable outcomes + subgroup axis come from the active vertical's config.
    # Without cfg, core has no domain field registry and therefore pools nothing.
    if cfg is not None:
        pf = cfg.poolable_fields()
        prop_fields = pf["proportion"]
        log_fields = pf["log_effect"]
        bin_fields = cfg.binary_2x2_fields()
        cont_fields = cfg.continuous_fields()
        sub_field, sub_map = cfg.subgroup_field, cfg.subgroup_map
        dose_outcome = dose_outcome or cfg.dose_outcome
    else:
        prop_fields = []
        log_fields = []
        bin_fields = []
        cont_fields = []
        sub_field, sub_map = "", None
        dose_outcome = ""

    notes: list[str] = []
    outcomes: list[StatsOutcome] = []
    hr_outcomes: list[HROutcome] = []
    # Curate BEFORE pooling, PER FIELD: among records that report THIS outcome,
    # collapse multiple data-cuts/companion reports of a trial to the most-recent
    # (no double-counting); for HR pools additionally fail closed on the comparator
    # anchor. Per-field so a newer report omitting an outcome can't delete an older
    # report's valid estimate for that outcome.
    anchor_terms = getattr(cfg, "anchor_terms", []) if cfg is not None else []
    # An anchored indirect comparison REQUIRES anchor terms to validate comparators;
    # without them the comparator gate is inert. FAIL CLOSED: suppress HR (log-effect)
    # pooling entirely rather than emit an unvalidated-comparator hazard-ratio pool.
    anchored_without_terms = (
        cfg is not None and getattr(cfg, "indirect_comparison", False) and not anchor_terms)
    if anchored_without_terms:
        notes.append(
            "HR pooling SUPPRESSED (fail-closed): indirect_comparison is configured but "
            "synthesis.anchor_terms is empty, so the comparator anchor cannot be verified. "
            "Set synthesis.anchor_terms to enable anchored hazard-ratio pools."
        )
    curation_drops: list[dict] = []
    metafor_ok = r_available()
    if metafor_ok:
        for fld, label in prop_fields:
            cur, drops = curate_for_pool(   # proportions: dedup only, among poolable
                extractions, fld, is_poolable=lambda es, f=fld: proportion_poolable(es, f))
            curation_drops += drops
            rows, excluded = outcome_rows(cur, fld, modality_by_pmid,
                                          subgroup_field=sub_field, subgroup_map=sub_map)
            if len(rows) < 2:
                continue
            png = fig_dir / f"forest_{fld}.png"
            try:
                res = run_metaprop(rows, label, png)
            except Exception:  # one bad outcome must not sink the rest
                outcomes.append(StatsOutcome(fld, label, [], 0, excluded))
                continue
            outcomes.append(StatsOutcome(
                field=fld, label=label, results=res,
                n_pooled=len(rows), n_excluded=excluded,
                forest_png=png.name if png.exists() else "",
            ))

        for fld, label in (log_fields if not anchored_without_terms else []):
            cur, drops = curate_for_pool(
                extractions, fld, anchor_terms=anchor_terms,
                is_poolable=lambda es, f=fld: hr_poolable(es, f))
            curation_drops += drops
            rows, excluded = hr_rows(cur, fld, modality_by_pmid,
                                     subgroup_field=sub_field, subgroup_map=sub_map)
            if len(rows) < 2:
                continue
            png = fig_dir / f"forest_{fld}.png"
            try:
                res = run_metahr(rows, label, png)
            except Exception:
                res = []
            diag = None
            try:
                from core.sr.stats.diagnostics import run_effect_diagnostics
                diag = run_effect_diagnostics(rows, fld, label, fig_dir)
            except Exception:
                diag = None
            hr_outcomes.append(HROutcome(fld, label, res, len(rows), excluded,
                                         png.name if png.exists() else "", diag))
        if curation_drops:
            notes.append(
                f"Pre-pool curation removed {len(curation_drops)} estimate(s) across "
                f"outcomes (per-field trial data-cut dedup + comparator-anchor filter)."
            )
    elif prop_fields or log_fields:
        notes.append("R/metafor not available — install with Rscript -e 'install.packages(\"metafor\")'.")

    bin_outcomes: list[BinOutcome] = []
    bin_ok = metabin_available()
    if bin_ok:
        for spec in bin_fields:
            rows, excluded = bin_rows(extractions, spec["pair"], modality_by_pmid,
                                      subgroup_field=sub_field, subgroup_map=sub_map)
            if len(rows) < 2:
                continue
            fld, label = spec["field"], spec["label"]
            png = fig_dir / f"forest_{fld}.png"
            try:
                res = run_metabin(rows, label, png, beneficial=_beneficial(spec.get("beneficial")))
            except Exception:
                res = []
            bin_outcomes.append(BinOutcome(fld, label, res, len(rows), excluded,
                                           png.name if png.exists() else ""))
    elif bin_fields:
        notes.append("R/meta not available for metabin — install with "
                     "Rscript -e 'install.packages(\"meta\")'.")

    cont_outcomes: list[ContOutcome] = []
    cont_ok = metacont_available()
    if cont_ok:
        for spec in cont_fields:
            rows, excluded = cont_rows(extractions, spec["pair"], modality_by_pmid,
                                       subgroup_field=sub_field, subgroup_map=sub_map)
            if len(rows) < 2:
                continue
            fld, label = spec["field"], spec["label"]
            png = fig_dir / f"forest_{fld}.png"
            try:
                res = run_metacont(rows, label, png)
            except Exception:
                res = []
            cont_outcomes.append(ContOutcome(fld, label, res, len(rows), excluded,
                                             png.name if png.exists() else ""))

    elif cont_fields:
        notes.append("R/meta not available for metacont — install with "
                     "Rscript -e 'install.packages(\"meta\")'.")

    method_available = (
        (metafor_ok and bool(prop_fields or log_fields))
        or (bin_ok and bool(bin_fields))
        or (cont_ok and bool(cont_fields))
    )
    if not method_available:
        return StatsResult(False, "; ".join(notes) or "No configured poolable outcomes.")

    # Network meta-analysis (Bayesian) — feasibility-gated, honest fallback.
    from core.sr.stats.nma import build_nma_edges, run_nma
    arms = cfg.synthesis_arms if cfg is not None else []
    nma = run_nma(build_nma_edges(extractions, modality_by_pmid, subgroup_map=sub_map),
                  fig_dir, target=tuple(arms) if len(arms) == 2 else None)

    # Dose-response (dosresmeta + RCS) — honesty-gated; no dose data -> "not performed".
    from core.sr.stats.dose_response import build_dose_data, run_dose_response
    dr = run_dose_response(build_dose_data(extractions, modality_by_pmid, dose_table),
                           dose_outcome or "", fig_dir)

    return StatsResult(
        True, "; ".join(notes), outcomes, hr_outcomes, bin_outcomes, cont_outcomes, nma, dr)
