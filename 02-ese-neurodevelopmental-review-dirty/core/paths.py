"""Domain-neutral output/path + env resolution for the evidence synthesis engine.

All engine I/O roots are configurable via env var and default to a neutral,
vertical-keyed location under ``~/Documents/EvidenceEngine``. Legacy ``ONCO_*``
and ``SR_*`` env names are honored as deprecated aliases.
"""

from __future__ import annotations

import os
from pathlib import Path


def env(new: str, *aliases: str, default: str = "") -> str:
    """First non-empty value among ``new`` then any deprecated ``aliases``, else default."""
    for key in (new, *aliases):
        val = os.environ.get(key)
        if val:
            return val
    return default


def path_env(new: str, *aliases: str, default: Path) -> Path:
    """Like :func:`env` but returns an expanded Path, falling back to ``default``."""
    val = env(new, *aliases)
    return Path(val).expanduser() if val else default


ENGINE_BASE = path_env("ESE_OUTPUT_DIR", "EVIDENCE_OUTPUT_DIR", default=Path("~/Documents/EvidenceEngine").expanduser())


def vertical_dir(name: str) -> Path:
    """Per-vertical output home under the engine base."""
    return ENGINE_BASE / (name or "default")
