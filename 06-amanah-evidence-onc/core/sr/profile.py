"""Review-type profiles — which phases run + question framing.

One pipeline, many review types: a Profile says whether each analysis phase
(rob/grade/meta/chart/prospero/narrative …) is part of this review type, so the
manuscript and pipeline include only what applies. Domain config (PICO/PCC,
search) stays in the vertical; review TYPE lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

_PATH = Path(__file__).resolve().parent / "profiles.yaml"


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    framing: str                 # "pico" | "pcc"
    phases: frozenset
    reporting: str = ""
    note: str = ""
    options: dict = field(default_factory=dict)

    def enabled(self, phase: str) -> bool:
        return phase in self.phases


@lru_cache(maxsize=1)
def _raw() -> dict:
    return yaml.safe_load(_PATH.read_text(encoding="utf-8")) or {}


def profile_names() -> list[str]:
    return list((_raw().get("profiles") or {}).keys())


def load_profile(name: str | None = None) -> Profile:
    raw = _raw()
    name = name or raw.get("default", "systematic")
    profiles = raw.get("profiles") or {}
    if name not in profiles:
        raise ValueError(f"Unknown review profile '{name}'. Options: {list(profiles)}")
    p = profiles[name]
    return Profile(
        name=name,
        label=p.get("label", name),
        framing=p.get("framing", "pico"),
        phases=frozenset(p.get("phases", [])),
        reporting=p.get("reporting", ""),
        note=p.get("note", ""),
        options=dict(p.get("options") or {}),
    )
