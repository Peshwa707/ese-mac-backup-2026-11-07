"""Render the Milestone-1 artifacts: PRISMA flow diagram, extraction table,
screening reconciliation sheet, and JSON. Templates live in the SR vertical.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.sr.config import SRConfig
from core.sr.models import SRRun


def _env(cfg: SRConfig) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(cfg.templates_dir)),
        autoescape=select_autoescape(["html", "svg", "xml"]),
    )


def render_prisma_flow(run: SRRun, cfg: SRConfig) -> str:
    return _env(cfg).get_template("prisma_flow.svg.j2").render(run=run, c=run.counts)


def render_extraction_table(run: SRRun, cfg: SRConfig) -> str:
    groups = ["identity", "population", "efficacy", "safety"]
    return _env(cfg).get_template("extraction_table.html.j2").render(
        run=run, cfg=cfg, groups=groups
    )


def render_screening_sheet(run: SRRun, cfg: SRConfig) -> str:
    return _env(cfg).get_template("screening_sheet.html.j2").render(run=run, cfg=cfg)


def render_json(run: SRRun) -> str:
    return run.model_dump_json(indent=2)


def write_all(run: SRRun, cfg: SRConfig, out_dirs: list[Path]) -> list[Path]:
    """Render every artifact and write it to each output directory."""
    artifacts = {
        f"{cfg.short_id}_prisma_flow.svg": render_prisma_flow(run, cfg),
        f"{cfg.short_id}_extraction_table.html": render_extraction_table(run, cfg),
        f"{cfg.short_id}_screening_sheet.html": render_screening_sheet(run, cfg),
        f"{cfg.short_id}_run.json": render_json(run),
    }
    written: list[Path] = []
    for out_dir in out_dirs:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, content in artifacts.items():
            path = out_dir / name
            path.write_text(content, encoding="utf-8")
            written.append(path)
    return written
