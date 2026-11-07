"""The registry: the one place that knows every tool.

Tools register themselves here at import time. Every surface (CLI now, recipes,
a future agent) reads this registry rather than hard-coding tool knowledge.
"""

from __future__ import annotations

from core.tools.spec import Tool

_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _TOOLS:
        raise ValueError(f"tool '{tool.name}' is already registered")
    _TOOLS[tool.name] = tool
    return tool


def get(name: str) -> Tool:
    if name not in _TOOLS:
        raise KeyError(f"unknown tool '{name}'. Known: {', '.join(sorted(_TOOLS))}")
    return _TOOLS[name]


def all_tools() -> list[Tool]:
    return [_TOOLS[n] for n in sorted(_TOOLS)]


def load_builtins() -> None:
    """Import the modules that register the engine's built-in tools (idempotent)."""
    import core.tools.builtin  # noqa: F401
