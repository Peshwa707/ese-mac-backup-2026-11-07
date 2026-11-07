"""What a 'tool' is — the single declarative shape every surface consumes.

A Tool wraps one validated capability of the engine. It declares which run-folder
files it reads and writes (so dependency order is checkable), which "brain" it uses
(deterministic math vs an AI model, cloud vs local), and its scorecard (how far it
has graduated through validation). Define a tool once here; the CLI, the recipes,
and a future agent all read the same registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class BackendPolicy:
    """Which brain a tool uses, and the safety rules around switching it."""
    default: str = "cloud"          # "cloud" | "local" | "harness" (ignored for deterministic tools)
    cloud_only: bool = False        # safety tools (e.g. claim-check): never forced local
    local_validated: bool = False   # has this tool's LOCAL version passed its benchmark?
    harness_enabled: bool = False   # can this tool pause for subscription-agent output?


@dataclass(frozen=True)
class Scorecard:
    """How far this tool has graduated through validation."""
    internally_tested: bool = False     # automated tests pass (does what it claims)
    externally_validated: bool = False  # matched a published benchmark (matches reality)
    benchmark_ref: str = ""             # where that validation is recorded

    @property
    def trusted_for_agent(self) -> bool:
        """The B->D gate: only fully-validated tools may be run by the auto-pilot."""
        return self.internally_tested and self.externally_validated


@dataclass(frozen=True)
class Tool:
    name: str                       # "screen", "pool-proportions", ...
    description: str                # one line; doubles as the agent-facing description
    kind: str                       # "deterministic" (math/rules) | "ai" (needs a model)
    input_artifacts: tuple[str, ...]  # run-folder files it reads
    output_artifact: str            # run-folder file it writes
    run: Callable[[Path, dict, str], Path]  # (run_dir, args, backend) -> output path
    backend: BackendPolicy = field(default_factory=BackendPolicy)
    scorecard: Scorecard = field(default_factory=Scorecard)
    args_help: str = ""             # human hint for the CLI
    conclusion_bearing: bool = False  # may render final certainty/conclusions

    def __post_init__(self):
        if self.kind not in ("deterministic", "ai"):
            raise ValueError(f"tool {self.name}: kind must be 'deterministic' or 'ai'")
        if self.kind == "deterministic" and self.backend.cloud_only:
            raise ValueError(f"tool {self.name}: deterministic tools have no brain to gate")
