from __future__ import annotations

import pytest

from benchmark.__main__ import main


def test_benchmark_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "frame" in out
    assert "answer-key" in out
    assert "harness" in out


def test_score_cli_exposes_allow_draft(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["score", "run", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--allow-draft" in out
