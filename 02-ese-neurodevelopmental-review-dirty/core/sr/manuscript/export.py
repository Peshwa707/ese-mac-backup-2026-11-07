"""Write a manuscript project folder under the engine output base.

The base is the vertical's ``review.output_base`` if set, else a neutral
per-vertical directory under ``~/Documents/EvidenceEngine/<vertical>/`` (override
the root with ``EVIDENCE_OUTPUT_DIR``). Each component is saved as Markdown (editable), DOCX, and PDF; a combined
manuscript, a PRISMA figure, a manifest.json, and an index.html landing page are
also written. PDF rendering degrades to HTML (in the pdf/ folder) with a recorded
warning if Chrome is unavailable — never a silent gap.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from core.render.render import html_to_pdf, pdf_available
from core.sr.config import SRConfig
from core.sr.manuscript.assemble import build_components
from core.sr.manuscript.render_docx import component_to_docx, components_to_docx
from core.sr.manuscript.render_html import component_to_html, components_to_html
from core.sr.manuscript.render_md import component_to_md, components_to_md
from core.sr.models import SRRun
from core.sr.render import render_prisma_flow
from core.paths import vertical_dir


def _slug(text: str, max_len: int = 52) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "review"


def _project_name(cfg: SRConfig, day: str, review_type: str = "") -> str:
    # Prefer a concise label over the full journal-style title; tag the review type
    # so systematic vs scoping vs rapid runs of the same topic/day don't collide.
    title = cfg.review_title.split(":")[0]
    suffix = f"_{review_type}" if review_type and review_type != "systematic" else ""
    return f"{_slug(title)}{suffix}_{day}"


def export_project(
    run: SRRun,
    cfg: SRConfig,
    records: dict[str, dict],
    *,
    audit: Optional[dict] = None,
    rob: Optional[list] = None,
    grade: Optional[list] = None,
    modality_by_pmid: Optional[dict] = None,
    prospero: Optional[dict] = None,
    narrative: Optional[dict] = None,
    references: Optional[dict] = None,
    stats=None,
    profile=None,
    chart=None,
    base_dir: Optional[Path] = None,
    day: Optional[str] = None,
    gate: Optional[tuple[bool, list[str]]] = None,
) -> dict:
    from core.sr.profile import load_profile
    profile = profile or load_profile()
    day = day or date.today().isoformat()

    # Export gate (orchestrator honesty gates). ``gate`` is None for legacy /
    # programmatic callers (ungated); (True, _) renders normally; (False, reasons)
    # SUPPRESSES the dossier — no full manuscript, no per-component renders — and
    # writes a notice recording why, so the withholding is never silent.
    if gate is not None and gate[0] is False:
        return write_blocked_export(cfg, gate[1], base_dir=base_dir, day=day,
                                    review_type=profile.name, run_id=run.run_id,
                                    provenance_id=run.provenance_id)

    base_dir = _resolve_base_dir(cfg, base_dir)
    project = base_dir / _project_name(cfg, day, profile.name)

    md_dir = project / "markdown"
    docx_dir = project / "docx"
    pdf_dir = project / "pdf"
    fig_dir = project / "figures"
    for d in (md_dir, docx_dir, pdf_dir, fig_dir):
        d.mkdir(parents=True, exist_ok=True)

    # PRISMA figure (referenced by the results component).
    (fig_dir / "prisma_flow.svg").write_text(render_prisma_flow(run, cfg), encoding="utf-8")
    if rob:
        from core.sr.rob_figure import traffic_light_svg
        (fig_dir / "rob_traffic_light.svg").write_text(traffic_light_svg(rob), encoding="utf-8")

    from core.sr.profile import load_profile
    profile = profile or load_profile()

    # Quantitative meta-analysis (Phase 4) — only if the profile runs it.
    if stats is None and modality_by_pmid is not None and profile.enabled("meta"):
        from core.sr.stats.run import meta_analyze
        stats = meta_analyze(run.extractions, modality_by_pmid, fig_dir, cfg=cfg)

    components = build_components(run, cfg, records, audit=audit, rob=rob, grade=grade,
                                  stats=stats, prospero=prospero, narrative=narrative,
                                  references=references, profile=profile, chart=chart, day=day)
    warnings: list[str] = []
    written: list[str] = []

    def _pdf(htmlstr: str, out: Path) -> None:
        try:
            out.write_bytes(html_to_pdf(htmlstr))
            written.append(str(out))
        except Exception as exc:  # degrade, never silent
            alt = out.with_suffix(".html")
            alt.write_text(htmlstr, encoding="utf-8")
            warnings.append(f"PDF degraded to HTML for {out.name}: {exc}")
            written.append(str(alt))

    for c in components:
        (md_dir / f"{c.key}.md").write_text(component_to_md(c), encoding="utf-8")
        component_to_docx(c).save(str(docx_dir / f"{c.key}.docx"))
        _pdf(component_to_html(c, figures_dir=fig_dir), pdf_dir / f"{c.key}.pdf")
        written.append(str(md_dir / f"{c.key}.md"))
        written.append(str(docx_dir / f"{c.key}.docx"))

    # Combined full manuscript.
    (md_dir / "99_full_manuscript.md").write_text(components_to_md(components), encoding="utf-8")
    components_to_docx(components).save(str(docx_dir / "99_full_manuscript.docx"))
    _pdf(components_to_html(components, figures_dir=fig_dir), pdf_dir / "99_full_manuscript.pdf")

    manifest = {
        "title": cfg.review_title,
        "review_type": profile.name,
        "reporting_standard": profile.reporting,
        "project": project.name,
        "run_id": run.run_id,
        "provenance_id": run.provenance_id,
        "date": day,
        "counts": run.counts.model_dump(),
        "honesty_audit": audit,
        "components": [{"key": c.key, "title": c.title} for c in components],
        "formats": ["markdown", "docx", "pdf"],
        "pdf_available": pdf_available(),
        "warnings": warnings,
    }
    (project / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (project / "index.html").write_text(_index_html(cfg, run, components, audit), encoding="utf-8")

    return {"project_dir": str(project), "warnings": warnings, "n_components": len(components)}


def _resolve_base_dir(cfg: SRConfig, base_dir: Optional[Path]) -> Path:
    # Per-vertical output home (cfg.output_base) overrides the engine default.
    return base_dir or (Path(cfg.output_base).expanduser() if cfg.output_base
                        else vertical_dir(cfg.root.name))


def write_blocked_export(cfg: SRConfig, reasons, *, base_dir: Optional[Path] = None,
                         day: Optional[str] = None, review_type: str = "systematic",
                         run_id: str = "", provenance_id: str = "") -> dict:
    """Suppress the manuscript render: emit only a notice + a blocked manifest.

    Called from EVERY export surface (export_project, the CLI, and the registered
    tool) so a blocked export is identical regardless of entry point. No
    markdown/docx/pdf component files and no full manuscript are written, and a
    prior ALLOWED export to this (deterministic) project dir is PURGED first —
    otherwise a downloadable manuscript + index.html would survive next to the
    block notice and still be shippable."""
    import shutil
    reasons = list(reasons)
    base_dir = _resolve_base_dir(cfg, base_dir)
    day = day or date.today().isoformat()
    project = base_dir / _project_name(cfg, day, review_type)
    if project.exists():
        for sub in ("markdown", "docx", "pdf", "figures"):
            shutil.rmtree(project / sub, ignore_errors=True)
        (project / "index.html").unlink(missing_ok=True)
        (project / "99_full_manuscript.md").unlink(missing_ok=True)
    project.mkdir(parents=True, exist_ok=True)
    notice = (
        f"# Export blocked — {cfg.review_title}\n\n"
        "The manuscript was **not rendered**. This run has not cleared its "
        "machine-enforced honesty gates, so the dossier is withheld to prevent "
        "shipping un-vetted output.\n\n"
        "## Unmet requirements\n\n"
        + "\n".join(f"- {r}" for r in reasons)
        + "\n\nClear the gates (a human reviewer must sign the required approvals) "
        "and re-export.\n"
    )
    (project / "EXPORT_BLOCKED.md").write_text(notice, encoding="utf-8")
    manifest = {
        "title": cfg.review_title,
        "review_type": review_type,
        "project": project.name,
        "run_id": run_id,
        "provenance_id": provenance_id,
        "date": day,
        "export_blocked": True,
        "block_reasons": reasons,
    }
    (project / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"project_dir": str(project), "warnings": [], "n_components": 0,
            "export_blocked": True, "block_reasons": reasons}


def _index_html(cfg: SRConfig, run: SRRun, components, audit: Optional[dict]) -> str:
    rows = []
    for c in components:
        rows.append(
            f"<tr><td>{_html.escape(c.title)}</td>"
            f"<td><a href='markdown/{c.key}.md'>md</a></td>"
            f"<td><a href='docx/{c.key}.docx'>docx</a></td>"
            f"<td><a href='pdf/{c.key}.pdf'>pdf</a></td></tr>"
        )
    c = run.counts
    audit_line = ""
    if audit:
        a = audit.get("anchoring", {})
        r = audit.get("resolution", {})
        audit_line = (
            f"<p><b>Honesty audit:</b> {r.get('resolved')}/{r.get('checked')} PMIDs resolve · "
            f"{a.get('anchored')}/{a.get('substantive_values')} values quote-anchored "
            f"({round(100 * a.get('anchored_rate', 0), 1)}%) · "
            f"{a.get('estimated_for_human_review')} ESTIMATED · {a.get('nr')} NR.</p>"
        )
    return f"""<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<title>{_html.escape(cfg.review_title)}</title>
<style>body{{font:14px/1.5 -apple-system,Helvetica,sans-serif;max-width:900px;margin:32px auto;padding:0 20px;color:#1a1a1a}}
table{{border-collapse:collapse;width:100%;margin-top:14px}}td,th{{border:1px solid #d6deea;padding:6px 10px;text-align:left}}
th{{background:#34507a;color:#fff}} a{{color:#23509a}} .full td{{background:#eef5ee;font-weight:600}}</style></head>
<body>
<h1>{_html.escape(cfg.review_title)}</h1>
<p>AI Reviewer-1 first pass · run {run.run_id[:8]} · {c.included} included / {c.screened} screened / {c.uncertain} uncertain.</p>
{audit_line}
<p><b>Combined manuscript:</b> <a href='markdown/99_full_manuscript.md'>md</a> ·
<a href='docx/99_full_manuscript.docx'>docx</a> · <a href='pdf/99_full_manuscript.pdf'>pdf</a></p>
<table><tr><th>Component</th><th>MD</th><th>DOCX</th><th>PDF</th></tr>
{''.join(rows)}
</table>
<p style='color:#6b7890;font-size:12px;margin-top:20px'>manifest.json · figures/prisma_flow.svg ·
Editable source = Markdown. RoB / Statistics / GRADE are Milestone-2/Phase-B stubs (no invented content).</p>
</body></html>"""
