"""Per-report provenance audit log (reproducible, defensible).

Writes the full query -> results -> verification -> rejection trail to a JSON
file under the provenance directory and returns its id.
"""

from __future__ import annotations

from pathlib import Path

from core.models import Provenance
from core.paths import path_env

_DEFAULT_DIR = path_env("EVIDENCE_PROVENANCE_DIR", "ONCO_PROVENANCE_DIR",
                        default=Path("/tmp/evidence-provenance"))


def store_dir() -> Path:
    # mode=0o700: provenance holds clinically-sensitive query/verification
    # trails; only the owning process user should be able to read or list them.
    _DEFAULT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Tighten permissions on an existing directory created with a looser umask.
    try:
        _DEFAULT_DIR.chmod(0o700)
    except OSError:
        pass  # best-effort; may fail on read-only or network-mounted paths
    return _DEFAULT_DIR


def write(provenance: Provenance, dir: Path | None = None) -> str:
    """Write the provenance record. When ``dir`` is given (e.g. a run dir) it is used
    instead of the global store, keeping the audit trail with the run (off /tmp)."""
    target = Path(dir) if dir is not None else store_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{provenance.report_id}.provenance.json"
    path.write_text(provenance.model_dump_json(indent=2), encoding="utf-8")
    # Restrict the individual file to owner-only as well.
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return provenance.report_id


def read(report_id: str) -> Provenance | None:
    path = store_dir() / f"{report_id}.provenance.json"
    if not path.exists():
        return None
    return Provenance.model_validate_json(path.read_text(encoding="utf-8"))
