"""Template-driven rendering to HTML / PDF / JSON.

Templates live in the active vertical. PDF uses Chrome headless print (the same
approach as the digest tooling); if no Chrome binary is present we degrade to
HTML and say so, rather than failing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.config.vertical import VerticalConfig
from core.models import Report

_CHROME_BINARIES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]
# macOS installs Chrome/Chromium as an .app bundle that is not on PATH; check the
# bundle binaries directly so PDF rendering works without a separate CLI install.
_CHROME_APP_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def _env(vertical: VerticalConfig) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(vertical.templates_dir)),
        autoescape=select_autoescape(["html"]),
    )


def render_html(report: Report, vertical: VerticalConfig, *, manuscript: bool = False) -> str:
    env = _env(vertical)
    # Template filenames are vertical-supplied (see VerticalConfig); core hardcodes none.
    template_name = vertical.report_template if manuscript else vertical.summary_template
    template = env.get_template(template_name)
    return template.render(report=report)


def render_json(report: Report) -> str:
    return report.model_dump_json(indent=2)


def _find_chrome() -> str | None:
    for name in _CHROME_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    for path in _CHROME_APP_PATHS:  # macOS .app bundles (not on PATH)
        if os.access(path, os.X_OK):
            return path
    return None


def html_to_pdf(html: str) -> bytes:
    """Render an arbitrary HTML string to PDF bytes via Chrome headless.

    Generic counterpart to ``render_pdf`` (which is Report-coupled); used by the
    manuscript exporter to turn component HTML into PDFs. Raises RuntimeError if
    no Chrome/Chromium binary is available.
    """
    chrome = _find_chrome()
    if chrome is None:
        raise RuntimeError("no Chrome/Chromium binary available for PDF rendering")
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "doc.html"
        pdf_path = Path(tmp) / "doc.pdf"
        html_path.write_text(html, encoding="utf-8")
        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            f"--print-to-pdf={pdf_path}",
            "--no-pdf-header-footer",
            html_path.as_uri(),
        ]
        from core.paths import env
        if env("EVIDENCE_CHROME_NO_SANDBOX", "ONCO_CHROME_NO_SANDBOX") == "1":
            cmd.insert(1, "--no-sandbox")
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return pdf_path.read_bytes()


def render_pdf(report: Report, vertical: VerticalConfig, *, manuscript: bool = False) -> bytes:
    """Render to PDF via Chrome headless. Raises RuntimeError if no Chrome.

    Security note: --no-sandbox disables Chrome's renderer process sandbox,
    which provides privilege-separation between the renderer and the host OS.
    It is only appropriate in locked-down containers that lack kernel
    user-namespace support (the prerequisite for Chrome's sandbox to function).
    We omit it by default and only add it when the operator explicitly sets
    EVIDENCE_CHROME_NO_SANDBOX=1 (alias: ONCO_CHROME_NO_SANDBOX), accepting the tradeoff consciously.
    Prefer a kernel with user-namespace support so the sandbox can stay on.
    """
    return html_to_pdf(render_html(report, vertical, manuscript=manuscript))


def pdf_available() -> bool:
    return _find_chrome() is not None
