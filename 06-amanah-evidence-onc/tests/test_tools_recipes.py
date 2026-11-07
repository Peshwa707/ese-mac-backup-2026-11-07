"""Recipe-layer safety contracts."""

from __future__ import annotations

import pytest

from core.tools import recipes, registry


def test_quick_look_has_no_conclusion_bearing_steps():
    registry.load_builtins()
    quick = recipes.get("quick-look")
    assert [s.tool for s in quick.steps] == [
        "search", "screen", "extract", "pool-configured", "quick-look-summary"]
    assert all(not registry.get(step.tool).conclusion_bearing for step in quick.steps)


def test_recipe_failure_stops_with_step_name(monkeypatch, tmp_path):
    seen = []

    def fake_run_tool(name, run_dir, args=None, override=None, posture=None):
        seen.append(name)
        if name == "screen":
            raise FileNotFoundError("missing corpus")
        return tmp_path / f"{name}.json"

    monkeypatch.setattr("core.tools.runner.run_tool", fake_run_tool)
    with pytest.raises(ValueError, match="failed at step 'screen'"):
        recipes.run_recipe("quick-look", tmp_path, {"retmax": 2})
    assert seen == ["search", "screen"]


def test_recipe_cli_lists_steps(capsys):
    from core.tools.cli import main

    main(["recipe", "list"])
    out = capsys.readouterr().out
    assert "quick-look" in out
    assert "full-review" in out
