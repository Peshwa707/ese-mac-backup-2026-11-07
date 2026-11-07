"""Loader for vertical (domain) configuration.

A vertical lives at ``verticals/<name>/`` and supplies ALL domain knowledge the
engine needs: the case schema, search strategies, guideline references, and the
render templates. ``core`` reads these — it never hardcodes them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Repo root = two levels up from this file (core/config/vertical.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERTICALS_DIR = _REPO_ROOT / "verticals"


class VerticalConfig:
    def __init__(self, name: str, root: Path):
        self.name = name
        self.root = root
        self.schema: dict[str, Any] = _load_yaml(root / "schema.yaml")
        self.search_strategies: dict[str, Any] = _load_yaml(root / "search_strategies.yaml")
        self.guideline_refs: dict[str, Any] = _load_yaml(root / "guideline_refs.yaml")

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    def _templates(self) -> dict[str, Any]:
        return self.schema.get("templates") or {}

    @property
    def report_template(self) -> str:
        """Filename of the manuscript/care-report template (vertical-supplied)."""
        return self._templates().get("report", "report.html")

    @property
    def summary_template(self) -> str:
        """Filename of the decision-support summary template (vertical-supplied)."""
        return self._templates().get("summary", "summary.html")

    @property
    def disclaimer(self) -> str:
        return self.search_strategies.get("disclaimer") or self.schema.get(
            "disclaimer", ""
        )

    def prompt(self, key: str, default: str = "") -> str:
        """Return the vertical's LLM system prompt for ``key``.

        All domain framing lives in the vertical's ``schema.yaml`` ``prompts:``
        section so core/ stays domain-agnostic. ``default`` is the neutral
        generic fallback core supplies when a vertical omits a prompt — the
        engine still functions, just without domain-specific voice.
        """
        prompts = self.schema.get("prompts") or {}
        value = prompts.get(key)
        return (value or default).strip()

    @property
    def connector_sources(self) -> tuple[str, ...]:
        """Source names the active vertical selects (boundary C4).

        core/ owns the class registry; this list owns the SELECTION. Empty tuple
        means "the vertical did not configure a set" — callers fall back to the
        core registry's full default in that case.
        """
        conns = self.schema.get("connectors") or {}
        return tuple(conns.get("sources") or ())

    @property
    def deid_caps_allowlist(self) -> tuple[str, ...]:
        """Domain ALL-CAPS abbreviations the de-id scrubber must NOT treat as
        names. Supplied by the vertical so core/grounding/deid stays generic."""
        deid = self.schema.get("deid") or {}
        return tuple(deid.get("caps_allowlist") or ())


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Vertical config missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=None)
def _load_vertical_cached(name: str) -> VerticalConfig:
    root = _VERTICALS_DIR / name
    if not root.is_dir():
        raise FileNotFoundError(f"Unknown vertical '{name}' (expected {root})")
    return VerticalConfig(name, root)


def load_vertical(name: str | None = None) -> VerticalConfig:
    """Load (and cache) a vertical config.

    The default name is resolved from the environment on EACH call before
    delegating to the cache, so the cache is keyed on the resolved string only —
    a later ``EVIDENCE_VERTICAL`` change is honored and ``load_vertical()`` and
    ``load_vertical("oncology")`` share one cache entry instead of two.
    ``ONCO_VERTICAL`` is still honored as a deprecated alias.
    """
    from core.paths import env
    resolved = name or env("EVIDENCE_VERTICAL", "ONCO_VERTICAL", default="oncology")
    return _load_vertical_cached(resolved)
