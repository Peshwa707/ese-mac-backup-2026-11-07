"""Assemble a grounded SRRun into the full set of manuscript components.

Data-driven components (PRISMA results, included abstracts, study characteristics,
references, supplementary) are built only from the verified run. Narrative
components (intro/discussion) and not-yet-done analyses (RoB/stats/GRADE) are
explicit editable stubs — grounded slots filled, prose/results marked for the
author. Nothing is asserted on the engine's behalf.
"""

from __future__ import annotations

from typing import Optional

from core.sr.config import SRConfig
from core.sr.manuscript.model import (
    BulletList,
    CodeBlock,
    Component,
    Figure,
    Heading,
    Note,
    Paragraph,
    Table,
)
from core.sr.models import GradeRow, RoBAssessment, ScreenDecision, SRRun

_TODO = "[AUTHOR TO COMPLETE]"
_PRISMA_FIG = "prisma_flow.svg"
_ROB_FIG = "rob_traffic_light.svg"


def _included(run: SRRun) -> list[str]:
    return [s.pmid for s in run.screening if s.decision == ScreenDecision.INCLUDE]


def _modality_of(run: SRRun, pmid: str) -> str:
    for s in run.screening:
        if s.pmid == pmid:
            return s.modality
    return "unclear"


_AI_PROSE = ("AI-drafted from the grounded evidence brief and adversarially citation-checked "
             "(every number traced to the pooled analysis; every [PMID] a verified included "
             "study). A DRAFT requiring author review and approval.")


def _title_abstract(run: SRRun, cfg: SRConfig, narrative: Optional[dict] = None) -> Component:
    c = run.counts
    methods = Paragraph(
        f"PubMed/MEDLINE was searched (full result set, no date/outcome filters). Records were "
        f"deduplicated and every PMID verified against the live PubMed API. Title/abstract screening "
        f"(AI Reviewer 1) applied the prespecified eligibility criteria; data were extracted at "
        f"abstract and (where open-access) full-text level. Independent human verification by the "
        f"reviewer-of-record is pending."
    )
    counts_p = Paragraph(
        f"Of {c.identified_pubmed} records identified and {c.after_dedup} after deduplication, "
        f"{c.screened} were screened: {c.included} included, {c.excluded_screening} excluded, "
        f"{c.uncertain} uncertain."
    )
    from core.sr.signoff import DRAFT_BANNER, is_locked
    if narrative:
        background = Paragraph(narrative.get("abstract_background", ""))
        results = Paragraph(narrative.get("abstract_results", ""))
        # Hard lock: the abstract Conclusions are a FINAL conclusion — withhold them
        # behind a DRAFT banner until human sign-off is recorded.
        conclusions = (Note(DRAFT_BANNER, kind="warning") if is_locked(run)
                       else Paragraph(narrative.get("abstract_conclusions", "")))
        prose_note = Note(_AI_PROSE, kind="warning")
    else:
        background = Note(_TODO + " — disease/topic burden, intervention landscape, rationale.", kind="todo")
        results = Note(_TODO + " — comparative efficacy/safety from the synthesis.", kind="todo")
        conclusions = Note(_TODO, kind="todo")
        prose_note = None
    blocks = [Note(run.disclaimer, kind="warning"), Paragraph(cfg.review_title),
              Heading(2, "Structured Abstract")]
    if prose_note:
        blocks.append(prose_note)
    blocks += [Heading(3, "Background"), background, Heading(3, "Methods"), methods,
               Heading(3, "Results"), counts_p, results, Heading(3, "Conclusions"), conclusions]
    return Component("00_title_abstract", "Title & Abstract", blocks)


def _introduction(cfg: SRConfig, narrative: Optional[dict] = None, framing: str = "pico") -> Component:
    intro_block = (Paragraph(narrative["introduction"]) if narrative and narrative.get("introduction")
                   else Note(_TODO + " — burden; rationale; objective.", kind="todo"))
    blocks = []
    if narrative:
        blocks.append(Note(_AI_PROSE, kind="warning"))
    framing_label = "PCC" if framing == "pcc" else "PICO"
    framing_lines = [ln for ln in cfg.framing_text(framing).split("\n") if ln]
    blocks += [intro_block, Heading(2, f"Review question ({framing_label})"), BulletList(framing_lines)]
    return Component("01_introduction", "Introduction", blocks)


def _discussion(run: SRRun, narrative: Optional[dict] = None) -> Component:
    if narrative and narrative.get("discussion"):
        return Component("09_discussion", "Discussion",
                         [Note(_AI_PROSE, kind="warning"), Paragraph(narrative["discussion"])])
    return Component("09_discussion", "Discussion", [
        Note(_TODO + " — main findings, comparison with prior reviews, strengths/limitations "
             "(indirectness, heterogeneity), clinical & research implications.", kind="todo"),
        Paragraph(f"Grounded inputs: {run.counts.included} included studies; "
                  f"{run.counts.uncertain} await full-text adjudication."),
    ])


def _methods(run: SRRun, cfg: SRConfig) -> Component:
    reg = cfg.pico.get("review", {}).get("registration", "")
    not_run = [f"{d.get('name')}: {d.get('note')}" for d in cfg.not_executed_databases]
    return Component("02_methods", "Methods", [
        Heading(2, "Protocol and registration"),
        Paragraph(reg or "PROSPERO registration pending."),
        Heading(2, "Eligibility criteria"),
        Paragraph("Include if ALL of the following hold:"),
        BulletList(cfg.inclusion_criteria),
        Paragraph("Exclude if ANY of the following holds:"),
        BulletList(cfg.exclusion_criteria),
        Heading(2, "Information sources and search strategy"),
        *( [Table(headers=["Database", "Records identified", "Verification"],
                  rows=[[k, v, ("live API" if k in ("pubmed", "europepmc", "clinicaltrials")
                                else "provenance-attested (imported export)")]
                        for k, v in sorted(run.per_source.items(), key=lambda x: -x[1])],
                  caption=f"Databases searched (executed: {', '.join(run.sources_executed) or 'pubmed'}; "
                          f"{run.n_attested} imported/attested records).")]
           if run.per_source else [] ),
        Paragraph("PubMed/MEDLINE was executed live (no date, outcome, or language filters):"),
        CodeBlock(cfg.pubmed_query),
        Paragraph("Databases with strings produced but not executed in the automated pass:"),
        BulletList(not_run or ["(none)"]),
        Heading(2, "Study selection"),
        Paragraph(
            f"AI acted as Reviewer 1, screening {run.counts.screened} verified records by title and "
            f"abstract against the criteria above; {run.counts.included} were included, "
            f"{run.counts.excluded_screening} excluded, {run.counts.uncertain} marked uncertain "
            f"(reserved for full-text/human adjudication). Independent human verification by a "
            f"reviewer-of-record is required."
        ),
        Heading(2, "Data extraction"),
        Paragraph(
            "Data were extracted at the abstract level into a prespecified dictionary. Values not "
            "stated in an abstract are marked NR; derived values are marked ESTIMATED; every "
            "non-NR value carries the verbatim supporting quote."
        ),
        Heading(2, "Limitations of this automated pass"),
        BulletList(run.limitations),
    ])


def _prisma_results(run: SRRun) -> Component:
    c = run.counts
    counts_tbl = Table(
        headers=["Stage", "n"],
        rows=[
            ["Identified (PubMed)", c.identified_pubmed],
            ["After deduplication", c.after_dedup],
            ["Removed (PMID unverified)", c.id_unverified_removed],
            ["Excluded by publication type (pre-screen)", c.prescreen_excluded],
            ["Screened (verified)", c.screened],
            ["Reports included", c.included],
            ["Distinct studies (trials)", c.studies_included or c.included],
            ["Excluded", c.excluded_screening],
            ["Uncertain (→ full-text)", c.uncertain],
        ],
        caption="PRISMA 2020 flow counts (live).",
    )
    dedup_note = []
    if c.studies_included and c.studies_included != c.included:
        dedup_note = [Note(f"Trial-level deduplication applied: {c.included} included reports map to "
                           f"{c.studies_included} distinct studies (trials); the meta-analysis pools "
                           f"one entry per trial. See dedup_report.json.", kind="info")]
    excl = Table(
        headers=["Exclusion reason (title/abstract)", "n"],
        rows=[[k, v] for k, v in sorted(run.exclusion_reasons.items(), key=lambda x: -x[1])],
        caption="Reasons for exclusion at screening.",
    )
    captured = ", ".join(t for t, hit in run.named_trial_capture.items() if hit) or "none"
    return Component("03_prisma_results", "Results — Study Selection (PRISMA)", [
        Figure(caption="PRISMA 2020 flow diagram.", svg_path=_PRISMA_FIG),
        counts_tbl,
        *dedup_note,
        excl,
        Paragraph(f"Named pivotal trials captured in the corpus: {captured}."),
    ])


def _included_abstracts(run: SRRun, records: dict[str, dict]) -> Component:
    blocks: list = [
        Paragraph(f"{run.counts.included} studies met inclusion criteria (AI Reviewer 1). "
                  f"Each is listed below with its verified PMID and abstract."),
    ]
    for i, pmid in enumerate(_included(run), 1):
        r = records.get(pmid, {})
        title = r.get("title") or "(title unavailable)"
        year = r.get("year") or "n.d."
        modality = _modality_of(run, pmid)
        blocks.append(Heading(3, f"{i}. {title}"))
        blocks.append(Paragraph(
            f"PMID: {pmid} · Year: {year} · Modality: {modality} · "
            f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        ))
        blocks.append(Paragraph(r.get("abstract") or "[no abstract available]"))
    return Component("04_included_abstracts", "Included Studies — Abstract List", blocks)


def _study_characteristics(run: SRRun, records: dict[str, dict]) -> Component:
    blocks: list = [
        Note("Abstract-level extraction. NR = not stated in abstract (often full-text only); "
             "ESTIMATED = derived. Every non-NR value carries its source quote. Requires "
             "independent human verification.", kind="warning"),
    ]
    for es in run.extractions:
        r = records.get(es.pmid, {})
        header = es.study_name or r.get("title", "")[:60] or es.pmid
        blocks.append(Heading(3, f"{header} (PMID {es.pmid}{', ' + es.modality if es.modality else ''})"))
        rows = []
        for v in es.values:
            flag = v.flag or ""
            val = v.value + (f"  «{v.quote}»" if v.quote else "")
            rows.append([v.group, v.field, val, flag])
        blocks.append(Table(headers=["Group", "Field", "Value (with quote)", "Flag"], rows=rows))
    if not run.extractions:
        blocks.append(Paragraph("No included studies to characterize."))
    return Component("05_study_characteristics", "Study Characteristics & Extracted Data", blocks)


def _stub(key: str, title: str, note: str, planned: list[str]) -> Component:
    return Component(key, title, [
        Note(note, kind="todo"),
        Paragraph("Planned for this section:"),
        BulletList(planned),
    ])


def _references(run: SRRun, records: dict[str, dict]) -> Component:
    blocks: list = [Paragraph("Included studies (verified PMIDs):")]
    items = []
    for i, pmid in enumerate(_included(run), 1):
        r = records.get(pmid, {})
        title = r.get("title") or "(title unavailable)"
        year = r.get("year") or "n.d."
        items.append(f"{title} ({year}). PMID: {pmid}. https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
    blocks.append(BulletList(items or ["(none)"]))
    return Component("10_references", "References (Included Studies)", blocks)


def _supplementary(run: SRRun, cfg: SRConfig, audit: Optional[dict]) -> Component:
    blocks: list = [
        Heading(2, "Full search strategy"),
        Paragraph("PubMed/MEDLINE (executed):"),
        CodeBlock(cfg.pubmed_query),
    ]
    for db in cfg.not_executed_databases:
        if db.get("query"):
            blocks.append(Paragraph(f"{db.get('name')} (string produced; NOT executed — {db.get('note','')}):"))
            blocks.append(CodeBlock(" ".join((db.get("query") or "").split())))
    blocks += [
        Note("Conference abstracts (field-relevant congresses) and full reference-mining of the "
             "prior reviews are flagged for manual completion — not run in the automated pass.", kind="todo"),
        Heading(2, "Excluded at screening (by reason)"),
        Table(headers=["Reason", "n"],
              rows=[[k, v] for k, v in sorted(run.exclusion_reasons.items(), key=lambda x: -x[1])]),
    ]
    if audit:
        anchor = audit.get("anchoring", {})
        res = audit.get("resolution", {})
        blocks += [
            Heading(2, "Honesty audit"),
            BulletList([
                f"Included PMIDs resolving live on PubMed: {res.get('resolved')}/{res.get('checked')}",
                f"Substantive extracted values: {anchor.get('substantive_values')}",
                f"Quote-anchored: {anchor.get('anchored')} "
                f"({round(100 * anchor.get('anchored_rate', 0), 1)}%)",
                f"Flagged ESTIMATED for human review: {anchor.get('estimated_for_human_review')}",
                f"Honestly NR (not in abstract): {anchor.get('nr')}",
            ]),
            Note("Audit produced by core/sr/audit.py; unresolved PMIDs and unanchored values are "
                 "listed in honesty_audit.json, not hidden.", kind="info"),
        ]
    return Component("11_supplementary", "Supplementary Materials", blocks)


def _risk_of_bias(rob: list[RoBAssessment]) -> Component:
    from collections import Counter

    tool_counts = Counter(a.tool for a in rob)
    overall_counts = Counter(a.overall or "unrated" for a in rob)
    blocks: list = [
        Note("AI Reviewer-1 risk-of-bias DRAFT. Tool auto-selected by study design "
             "(RoB 2.0 / ROBINS-I / NOS). Every judgment requires human confirmation.", kind="warning"),
        Figure(caption="Risk-of-bias traffic-light summary (per study × domain).", svg_path=_ROB_FIG),
        Table(headers=["Tool", "Studies"], rows=[[k, v] for k, v in tool_counts.items()],
              caption="Tool assignment by study design."),
        Table(headers=["Overall judgment", "Studies"], rows=[[k, v] for k, v in overall_counts.most_common()],
              caption="Overall risk-of-bias distribution."),
        Heading(2, "Per-study judgments"),
    ]
    rows = []
    for a in rob:
        doms = "; ".join(f"{d.key}:{d.judgment}" for d in a.domains)
        rows.append([a.pmid, a.tool, a.overall, doms])
    blocks.append(Table(headers=["PMID", "Tool", "Overall", "Domain judgments"], rows=rows))
    return Component("06_risk_of_bias", "Risk of Bias Assessment", blocks)


def _ai_reporting(run: SRRun, audit: Optional[dict], profile, day: str) -> Component:
    from core.sr.ai_reporting import build_ai_reporting

    r = build_ai_reporting(run, audit, profile, day)
    return Component("13_ai_reporting", "AI Use Statement (RAISE / PRISMA-trAIce)", [
        Note("Transparent reporting of AI use per the 2025 Cochrane/Campbell/JBI/CEE position "
             "statement (RAISE) and the PRISMA-trAIce checklist.", kind="info"),
        Paragraph(f"AI tools: {r['tools']}"),
        Paragraph(f"Review type: {r['review_type']}  ·  Dates of AI use: {r['dates']}"),
        Heading(2, "Where AI was (and was not) used"),
        Table(headers=["Stage", "AI used", "Model", "Role"],
              rows=[[s["stage"], s["ai"], s["model"], s["role"]] for s in r["stages"]]),
        Heading(2, "Human oversight"),
        Paragraph(r["human_oversight"]),
        Heading(2, "Validation evidence"),
        BulletList(r["validation"]),
        Heading(2, "Reproducibility"),
        Paragraph(r["reproducibility"]),
        Heading(2, "Limitations of AI assistance"),
        Paragraph(r["limitations"]),
    ])


def _charting(run: SRRun, records: dict[str, dict]) -> Component:
    """Scoping-review evidence map: chart WHAT/WHERE research exists (PRISMA-ScR).

    No quality appraisal (that's deliberate for scoping) — tabular + categorical
    mapping across modality, study design, and year, from the included set.
    """
    from collections import Counter

    inc = [s for s in run.screening if s.decision == ScreenDecision.INCLUDE]
    by_modality = Counter(s.modality for s in inc)
    by_design = Counter(s.study_design for s in inc)
    by_year = Counter(str(records.get(s.pmid, {}).get("year") or "n.d.") for s in inc)
    return Component("06_evidence_map", "Evidence Map (Charting)", [
        Note("Scoping-review charting (PRISMA-ScR): mapping the extent, range, and nature of the "
             "evidence. No risk-of-bias or certainty appraisal is performed for a scoping review.", kind="info"),
        Heading(2, "By intervention modality"),
        Table(headers=["Modality", "Studies"], rows=[[k, v] for k, v in by_modality.most_common()]),
        Heading(2, "By study design"),
        Table(headers=["Design", "Studies"], rows=[[k, v] for k, v in by_design.most_common()]),
        Heading(2, "By publication year"),
        Table(headers=["Year", "Studies"], rows=[[k, v] for k, v in sorted(by_year.items())]),
    ])


def _prospero(prospero: dict) -> Component:
    fields = prospero.get("fields", [])
    dup = prospero.get("dup")
    blocks: list = [
        Note("PROSPERO registration DRAFT. Content fields are populated from the protocol; "
             "[HUMAN] fields (investigators, dates, funding) must be completed before a human "
             "submits this to PROSPERO. Submission + approval is a human gate.", kind="warning"),
        Heading(2, "Step 1 — Duplicate / overlap check"),
    ]
    if dup is None:
        blocks.append(Paragraph("Not run."))
    elif dup.get("status") == "checked":
        blocks.append(Paragraph(dup.get("note", "")))
        titles = dup.get("titles") or []
        blocks.append(BulletList(titles) if titles else Paragraph("No overlapping records parsed."))
    else:
        blocks.append(Note(dup.get("note", "Search PROSPERO manually."), kind="todo"))
    blocks.append(Heading(2, "Step 2 — Registration fields"))
    blocks.append(Table(headers=["Field", "Value"], rows=[[k, v] for k, v in fields],
                        caption="PROSPERO registration draft (22 core fields)."))
    return Component("12_prospero_registration", "PROSPERO Registration (Draft)", blocks)


def _statistical_analysis(stats) -> Component:
    blocks: list = [
        Note("Random-effects meta-analysis via R metafor (REML). Single-arm binary outcomes are pooled "
             "as Freeman-Tukey double-arcsine proportions; ratio effect measures (RR/OR/HR) are pooled by "
             "generic inverse-variance on the log scale, overall and by stratum. Studies whose value or "
             "denominator could not be parsed were excluded and counted. Leave-one-out sensitivity and "
             "small-study/publication-bias diagnostics (Egger/Begg/trim-and-fill) are reported per pooled "
             "effect, the latter performed only at k>=10 (Cochrane/Sterne 2011). AI Reviewer-1 draft — "
             "confirm before use.", kind="warning"),
    ]
    pooled_any = False
    for oc in stats.outcomes:
        if not oc.results:
            continue
        pooled_any = True
        blocks.append(Heading(2, oc.label))
        rows = [[r.group, r.k, f"{r.prop:.1f}%", f"{r.ci_lb:.1f}–{r.ci_ub:.1f}%", f"{r.I2:.0f}%"]
                for r in oc.results]
        blocks.append(Table(headers=["Group", "k", "Pooled", "95% CI", "I²"], rows=rows,
                            caption=f"{oc.label}: pooled proportion (n={oc.n_pooled} studies, "
                                    f"{oc.n_excluded} excluded as unparseable)."))
        if oc.forest_png:
            blocks.append(Figure(caption=f"Forest plot — {oc.label}.", svg_path=oc.forest_png))
    for oc in getattr(stats, "hr_outcomes", []):
        if not oc.results:
            continue
        pooled_any = True
        blocks.append(Heading(2, oc.label))
        rows = [[r.group, r.k, f"{r.hr:.2f}", f"{r.ci_lb:.2f}–{r.ci_ub:.2f}", f"{r.I2:.0f}%"]
                for r in oc.results]
        _is_ratio = oc.field.startswith("effect_")  # RR/OR (epi) vs HR (time-to-event)
        _measure = "relative risk (RR/OR)" if _is_ratio else "hazard ratio"
        blocks.append(Table(headers=["Group", "k", "Pooled estimate", "95% CI", "I²"], rows=rows,
                            caption=f"{oc.label}: inverse-variance pooled {_measure} "
                                    f"(n={oc.n_pooled} studies, {oc.n_excluded} excluded as unparseable)."))
        if oc.forest_png:
            blocks.append(Figure(caption=f"Forest plot — {oc.label}.", svg_path=oc.forest_png))
        dg = getattr(oc, "diagnostics", None)
        if dg is not None:
            if dg.leave_one_out:
                his = [l.hr for l in dg.leave_one_out]
                sig = all(l.ci_lb > 1 for l in dg.leave_one_out) or all(l.ci_ub < 1 for l in dg.leave_one_out)
                blocks.append(Paragraph(
                    f"Leave-one-out sensitivity (k={dg.k}): the pooled estimate ranges "
                    f"{min(his):.2f}–{max(his):.2f} across single-study omissions; "
                    + ("significance is preserved in every iteration." if sig
                       else "significance is not preserved in all iterations.")))
            pb = f"Publication bias — {dg.pubbias_note}."
            if dg.egger_p is not None:
                pb += (f" Egger p={dg.egger_p}; Begg p={dg.begg_p}; trim-and-fill adjusted "
                       f"{dg.trimfill_hr} ({dg.trimfill_lb}–{dg.trimfill_ub}), {dg.n_imputed} studies imputed.")
            blocks.append(Note(pb, kind="warning"))
            if dg.funnel_png:
                blocks.append(Figure(caption=f"Funnel plot — {oc.label}.", svg_path=dg.funnel_png))
    nma = getattr(stats, "nma", None)
    if nma is not None:
        blocks.append(Heading(2, "Network meta-analysis (Bayesian)"))
        if nma.feasible and nma.league:
            blocks.append(Table(headers=["Comparison", "Estimate (logHR)", "95% CrI"],
                                rows=[[r["comparison"], f"{r['estimate']:.2f}",
                                       f"{r['ci_lb']:.2f}–{r['ci_ub']:.2f}"] for r in nma.league],
                                caption="NMA league table (random-effects, gemtc)."))
            if nma.network_png:
                blocks.append(Figure(caption="Network diagram.", svg_path=nma.network_png))
        else:
            blocks.append(Note(f"NMA not performed — {nma.reason} No indirect estimate is "
                               f"fabricated.", kind="todo"))
    dr = getattr(stats, "dose_response", None)
    if dr is not None:
        blocks.append(Heading(2, "Dose-response meta-analysis"))
        if dr.feasible and dr.curve:
            pts = dr.curve[:: max(1, len(dr.curve) // 8)]
            blocks.append(Table(headers=["Dose", f"RR ({dr.outcome})", "95% CI"],
                                rows=[[f"{p['dose']:.2f}", f"{p['est']:.2f}",
                                       f"{p['ci_lb']:.2f}–{p['ci_ub']:.2f}"] for p in pts],
                                caption=f"Restricted-cubic-spline dose-response (dosresmeta), "
                                        f"dose unit '{dr.dose_unit}'"
                                        + (f"; non-linearity p={dr.p_nonlinearity:.3f}" if dr.p_nonlinearity is not None else "")))
            if dr.plot_png:
                blocks.append(Figure(caption=f"Dose-response curve — {dr.outcome}.", svg_path=dr.plot_png))
        else:
            blocks.append(Note(f"Dose-response not performed — {dr.reason} No curve is fabricated.",
                               kind="todo"))
    if not pooled_any and nma is None and dr is None:
        blocks.append(Paragraph("Insufficient parseable data to pool any outcome at this stage."))
    return Component("07_statistical_analysis", "Statistical Analysis", blocks)


def _grade_sof(grade: list[GradeRow], locked: bool = True) -> Component:
    from core.sr.signoff import DRAFT_CERTAINTY
    rows = []
    for g in grade:
        downs = "; ".join(g.downgrades) if g.downgrades else "—"
        # Hard lock: the computed certainty stays in the data (g.certainty / JSON) but
        # is withheld from the rendered SoF until human sign-off is recorded.
        certainty = DRAFT_CERTAINTY if locked else g.certainty
        rows.append([g.outcome, f"{g.n_studies} ({g.n_rct} RCT)", g.start, certainty, downs])
    deferred = grade[0].deferred if grade and grade[0].deferred else []
    blocks: list = [
        Note("AI Reviewer-1 GRADE DRAFT, computed deterministically from contributing studies' "
             "designs and risk-of-bias profile (starting certainty set by design — High for "
             "randomized, Low for observational). Domains needing pooled statistics "
             "(inconsistency, publication bias) and any upgrades are deferred below. Requires "
             "human confirmation.", kind="warning"),
        Table(headers=["Outcome", "Studies", "Starting", "Certainty", "Downgrades"], rows=rows,
              caption="GRADE Summary of Findings (certainty of evidence by outcome)."),
    ]
    if deferred:
        blocks.append(Heading(2, "Domains deferred to quantitative synthesis (Phase B)"))
        blocks.append(BulletList(deferred))
    return Component("08_grade", "GRADE Certainty of Evidence", blocks)


def build_components(
    run: SRRun, cfg: SRConfig, records: dict[str, dict], audit: Optional[dict] = None,
    rob: Optional[list[RoBAssessment]] = None,
    grade: Optional[list[GradeRow]] = None,
    stats=None,
    prospero: Optional[dict] = None,
    narrative: Optional[dict] = None,
    profile=None,
    chart: Optional[Component] = None,
    day: str = "",
) -> list[Component]:
    """Author the full manuscript as ordered components, gated by the review profile."""
    from core.sr.profile import load_profile
    profile = profile or load_profile()
    stats_component = _statistical_analysis(stats) if (stats and stats.available) else _stub(
        "07_statistical_analysis", "Statistical Analysis",
        "Phase B — NO pooled estimates, forest plots, or NMA have been computed. The "
        "extraction tables are emitted in stats-ready form; no numbers are invented here.",
        ["Pooled proportions (Freeman-Tukey) / inverse-variance effects, random-effects REML",
         "Heterogeneity: I², Cochran's Q, tau²", "Subgroup analysis + meta-regression (by the synthesis axis)",
         "Bayesian NMA via shared comparators (if feasible)", "Funnel plot / Egger's test"])
    from core.sr.signoff import is_locked
    locked = is_locked(run)
    grade_component = _grade_sof(grade, locked) if grade else _stub(
        "08_grade", "GRADE Certainty of Evidence",
        "Milestone 2 — certainty has NOT yet been rated.",
        ["Per-outcome certainty (High/Moderate/Low/Very Low)",
         "Indirectness downgrade where applicable", "Summary of Findings table"])
    rob_component = _risk_of_bias(rob) if rob else _stub(
        "06_risk_of_bias", "Risk of Bias Assessment",
        "Milestone 2 — risk of bias has NOT yet been assessed. No judgments are presented.",
        ["RoB 2.0 for RCTs", "ROBINS-I for non-randomized comparative studies",
         "Newcastle-Ottawa / JBI for single-arm studies", "Traffic-light summary figure"])
    comps = [
        _title_abstract(run, cfg, narrative),
        _introduction(cfg, narrative, profile.framing),
        _methods(run, cfg),
        _prisma_results(run),
        _included_abstracts(run, records),
        _study_characteristics(run, records),
    ]
    # Analysis components are included only if the review profile runs that phase.
    if profile.enabled("rob"):
        comps.append(rob_component)
    if profile.enabled("meta"):
        from core.sr.review_flags import selective_extraction_flags
        flags = selective_extraction_flags(stats, run.counts.included)
        if flags:
            stats_component.blocks.insert(0, Note(
                "Outcomes flagged for human review (selective-extractability check):\n- "
                + "\n- ".join(flags), kind="warning"))
        comps.append(stats_component)
    if profile.enabled("grade"):
        comps.append(grade_component)
    if profile.enabled("chart"):
        comps.append(chart if chart is not None else _charting(run, records))
    comps += [_discussion(run, narrative), _references(run, records), _supplementary(run, cfg, audit)]
    if profile.enabled("prospero") and prospero:
        comps.append(_prospero(prospero))
    comps.append(_ai_reporting(run, audit, profile, day))   # RAISE / PRISMA-trAIce — always
    return comps
