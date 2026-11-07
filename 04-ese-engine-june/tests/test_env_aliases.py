"""Test ESE_* env vars with backward-compatible aliases."""

import os

from core.paths import env, path_env
from core.sr.config import resolve_vertical
from core.sr.stats.meta import rscript_bin


def test_ese_vertical_alias(monkeypatch):
    monkeypatch.delenv("ESE_VERTICAL", raising=False)
    monkeypatch.setenv("SR_VERTICAL", "mm-bcma-early-relapse")
    assert resolve_vertical() == "mm-bcma-early-relapse"


def test_ese_rscript_alias(monkeypatch):
    monkeypatch.delenv("ESE_RSCRIPT", raising=False)
    monkeypatch.setenv("SR_RSCRIPT", "/usr/local/bin/Rscript")
    assert rscript_bin() == "/usr/local/bin/Rscript"


def test_ese_output_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ESE_OUTPUT_DIR", str(tmp_path / "out"))
    from importlib import reload
    import core.paths as paths
    reload(paths)
    assert paths.ENGINE_BASE == tmp_path / "out"


def test_chrome_no_sandbox_alias(monkeypatch):
    monkeypatch.setenv("ONCO_CHROME_NO_SANDBOX", "1")
    assert env("ESE_CHROME_NO_SANDBOX", "EVIDENCE_CHROME_NO_SANDBOX", "ONCO_CHROME_NO_SANDBOX") == "1"
