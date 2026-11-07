"""PDF rendering via Chrome headless.

Used by the manuscript exporter to turn component HTML into PDFs. If no Chrome
binary is present, callers degrade to HTML rather than failing silently.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_CHROME_BINARIES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]
_CHROME_APP_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def _find_chrome() -> str | None:
    for name in _CHROME_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    for path in _CHROME_APP_PATHS:
        if os.access(path, os.X_OK):
            return path
    return None


def html_to_pdf(html: str) -> bytes:
    """Render an arbitrary HTML string to PDF bytes via Chrome headless."""
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
        if env("ESE_CHROME_NO_SANDBOX", "EVIDENCE_CHROME_NO_SANDBOX", "ONCO_CHROME_NO_SANDBOX") == "1":
            cmd.insert(1, "--no-sandbox")
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return pdf_path.read_bytes()


def pdf_available() -> bool:
    return _find_chrome() is not None
