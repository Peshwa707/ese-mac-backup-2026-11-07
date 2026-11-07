"""Single hardened reader for the providers.env secrets file.

Both the LLM gateway (ANTHROPIC/OPENAI/... keys) and the search connectors (WoS/Scopus
keys) read the same ``~/.config/ese/providers.env``. This module is the ONE place that
resolves its location and reads a key fail-closed, so the two channels can never diverge
on where the file lives or how strictly it is checked.

Hardening (fail closed):
- opened with ``O_NOFOLLOW`` and validated on the file descriptor (``fstat``), so a
  symlinked secret file is refused and there is no check-then-read TOCTOU window;
- refuses group/other-accessible perms (``st_mode & 0o077``) and a non-owner file;
- comment (``#``) and blank lines skipped; surrounding quotes stripped from values.

Callers check ``os.environ`` first (process-local, legitimately unchecked) and only fall
back to the file. A missing file/key returns ``None``; an insecurely-stored file raises.
"""

from __future__ import annotations

import os
from pathlib import Path


def providers_file() -> Path:
    """Resolve the providers.env path (env override, then known locations)."""
    env = os.environ.get("ESE_PROVIDERS_ENV") or os.environ.get("ONCO_PROVIDERS_ENV")
    if env:
        return Path(env)
    for candidate in (
        Path.home() / ".config" / "ese" / "providers.env",
        Path.home() / ".config" / "onco-engine" / "providers.env",
    ):
        if candidate.exists():
            return candidate
    return Path.home() / ".config" / "ese" / "providers.env"


def read_secure_env(name: str, path: Path | None = None) -> str | None:
    """Return key ``name`` from the providers.env, or ``None`` if absent.

    Raises ``RuntimeError`` if the file exists but is insecurely stored (symlink,
    group/other-readable, or not owned by the current user). Does NOT consult the
    environment — the caller checks ``os.environ`` first.
    """
    f = path or providers_file()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(f, flags)
    except FileNotFoundError:
        return None
    except OSError as e:                         # ELOOP: final component is a symlink
        raise RuntimeError(f"{f} is a symlink or unreadable (refused): {e}") from e
    try:
        info = os.fstat(fd)                       # validate the OPENED fd — no TOCTOU
        if info.st_mode & 0o077:
            raise RuntimeError(f"{f} permissions too open (must be 0600)")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RuntimeError(f"{f} not owned by the current user (refused)")
        fh = os.fdopen(fd, "r", encoding="utf-8")
        fd = -1                                   # ownership transferred BEFORE the read,
        with fh:                                  # so a read error can't double-close the fd
            text = fh.read()
    finally:
        if fd >= 0:
            os.close(fd)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition("=")
        if sep and k.strip() == name and v.strip():
            return v.strip().strip('"').strip("'")
    return None
