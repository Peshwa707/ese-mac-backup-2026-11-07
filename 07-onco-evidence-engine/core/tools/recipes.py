"""Fixed, validated tool recipes.

Recipes are intentionally boring: ordered tool names plus shared args. They do
not bypass the registry or runner, so tool scorecards, backend routing, input
artifact checks, and conclusion locks remain the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from core.tools.harness import HarnessWorkPending
from core.tools import registry, runner
from core.tools.spec import Scorecard


@dataclass(frozen=True)
class RecipeStep:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    steps: tuple[RecipeStep, ...]
    scorecard: Scorecard = field(default_factory=lambda: Scorecard(internally_tested=True))


QUICK_LOOK_WARNING = (
    "Rapid scan of top N of M records - NOT a complete review; run Full Review before "
    "clinical or publication conclusions."
)


_RECIPES: dict[str, Recipe] = {
    "quick-look": Recipe(
        name="quick-look",
        description="Rapid non-final scan: search, screen, extract, pool configured outcomes, summarize.",
        steps=(
            RecipeStep("search", {"retmax_default": 25}),
            RecipeStep("screen"),
            RecipeStep("extract"),
            RecipeStep("pool-configured"),
            RecipeStep("quick-look-summary"),
        ),
    ),
    "full-review": Recipe(
        name="full-review",
        description="Full tool workflow through manuscript export.",
        steps=(
            RecipeStep("search"),
            RecipeStep("screen"),
            RecipeStep("extract"),
            RecipeStep("fulltext"),
            RecipeStep("risk-of-bias"),
            RecipeStep("grade"),
            RecipeStep("pool-configured"),
            RecipeStep("narrative"),
            RecipeStep("manuscript"),
        ),
    ),
}


def get(name: str) -> Recipe:
    if name not in _RECIPES:
        raise KeyError(f"unknown recipe '{name}'. Known: {', '.join(sorted(_RECIPES))}")
    return _RECIPES[name]


def all_recipes() -> list[Recipe]:
    return [_RECIPES[n] for n in sorted(_RECIPES)]


def _step_args(recipe: Recipe, step: RecipeStep, base_args: dict) -> dict:
    args = dict(base_args)
    args.update(step.args)
    if "retmax_default" in args:
        args["retmax"] = args.get("retmax") or args.pop("retmax_default")
    if recipe.name == "quick-look":
        args.pop("approve_conclusions", None)
    return {k: v for k, v in args.items() if v is not None and not k.endswith("_default")}


def run_recipe(name: str, run_dir: Path, args: dict | None = None,
               override: str | None = None, posture: str | None = None) -> list[Path]:
    recipe = get(name)
    outputs: list[Path] = []
    for step in recipe.steps:
        try:
            outputs.append(runner.run_tool(
                step.tool, run_dir, _step_args(recipe, step, args or {}),
                override=override, posture=posture))
        except HarnessWorkPending as exc:
            instr = (
                f"AWAITING AGENT: recipe '{name}' paused at step '{step.tool}' for "
                f"harness agent ({exc.source}): fill {exc.request_path}, write "
                f"{exc.expected_output} in {exc.request_path.parent}, then re-run."
            )
            raise HarnessWorkPending(
                tool=step.tool, stage=exc.stage, source=exc.source,
                request_path=exc.request_path, expected_output=exc.expected_output,
                instruction=instr) from exc
        except (FileNotFoundError, KeyError, ValueError, ValidationError) as exc:
            raise ValueError(f"recipe '{name}' failed at step '{step.tool}': {exc}") from exc
    return outputs
