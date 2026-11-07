"""Read search-database API credentials from the environment or the hardened
``~/.config/ese/providers.env`` secrets file.

Live API connectors for licensed databases (Web of Science, Scopus) need an API
key (and, for Scopus, an institutional token). Keys are read via the SAME shared
hardened reader (``core.secrets``) the LLM gateway uses, so the two channels can
never diverge on where the file lives or how strictly it is checked (0600 /
no-symlink / owner, TOCTOU-safe).

A missing credential returns ``None`` (the connector then degrades to an
`unavailable` result) — it is never an error to simply not have configured a key.
"""

from __future__ import annotations

import os

from core.secrets import read_secure_env


def search_credential(name: str) -> str | None:
    """Return the value of credential ``name`` from ``os.environ`` (checked first)
    or the hardened providers.env, else ``None``. Raises only if the secrets file
    exists but is insecurely stored (fail closed)."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    return read_secure_env(name)
