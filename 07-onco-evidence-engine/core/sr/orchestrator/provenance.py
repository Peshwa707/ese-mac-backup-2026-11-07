"""Run-directory reproducibility provenance (WP-M1.6, spec §6/§8).

Everything needed to audit and *replay* a run lives under ``runs/<id>/``:
  - provenance.json     per-stage models + hashes + gate records + extras
  - environment.json    engine git SHA, python/R/package versions, platform
  - search_manifest.json frozen returned-ID set + queries (replay vs a live re-query)
  - llm_io/<stage>.jsonl per-call request/response records, SECRET-SCRUBBED

LLM stages are tagged ``stochastic`` (replayable/auditable, not bit-reproducible);
deterministic stages reproduce exactly. No wall-clock is read here (timestamps are
passed in) so the module stays testable and deterministic.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from core.sr.orchestrator.adapters import write_json
from core.sr.orchestrator.hashing import hash_json

# Secret shapes redacted from any persisted llm_io (defence-in-depth; keys should
# never reach here, but raw provider payloads can carry Authorization headers).
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                      # Google API keys
    re.compile(r"(?i)api[_-]?key\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{12,}"),
]


# Dict keys whose VALUE is a secret regardless of its shape (token COUNT fields like
# "tokens"/"input_tokens" are deliberately NOT matched).
_SECRET_KEY_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|apikey|secret|password|access[_-]?key|bearer|credential|"
    r"session|cookie|private[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token)")
    # NB: bare "token"/"tokens"/"input_tokens" (usage counts) are deliberately NOT matched


def scrub_text(s: str) -> str:
    for p in _SECRET_PATTERNS:
        s = p.sub("[REDACTED]", s)
    return s


def _scrub(obj):
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = "[REDACTED]"          # secret-bearing key -> redact value wholesale
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    return obj


def append_llm_io(run_dir: Path, stage: str, record: dict) -> Path:
    """Append one secret-scrubbed LLM-call record to ``llm_io/<stage>.jsonl``."""
    p = Path(run_dir) / "llm_io" / f"{stage}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(_scrub(record), sort_keys=True, default=str) + "\n")
    return p


def _git(args: list[str], repo_dir: Path | None) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(repo_dir) if repo_dir else None,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _pkg_versions(names: list[str]) -> dict[str, str]:
    out = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except Exception:
            out[n] = "absent"
    return out


def _r_version() -> str:
    try:
        out = subprocess.run(["R", "--version"], capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines()[0].strip() if out.returncode == 0 and out.stdout else "unavailable"
    except Exception:
        return "unavailable"


def environment_snapshot(*, timestamp: str, repo_dir: Path | None = None) -> dict:
    """Capture the reproducibility environment. Best-effort: missing tools degrade to
    'unknown'/'absent'/'unavailable' rather than raising."""
    status = _git(["status", "--porcelain"], repo_dir)        # run once (avoid races)
    return {
        "timestamp_utc": timestamp,
        "git_sha": _git(["rev-parse", "HEAD"], repo_dir),
        "git_dirty": bool(status.strip()) if status != "unknown" else False,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": _pkg_versions(["pydantic", "httpx", "pyyaml", "metapub"]),
        "r_version": _r_version(),
    }


def write_environment(run_dir: Path, *, timestamp: str, repo_dir: Path | None = None) -> Path:
    path, _ = write_json(run_dir, "environment.json",
                         environment_snapshot(timestamp=timestamp, repo_dir=repo_dir))
    return path


def _record_entry(c) -> dict:
    """Canonical, typed, hashed record so replay can distinguish PMID/DOI/source and
    detect retrieved-metadata drift."""
    id_type = getattr(c.id_type, "value", str(c.id_type))
    doi = getattr(c, "doi", None)
    core = {"id": c.id, "id_type": id_type, "source": c.source, "doi": doi,
            "title": getattr(c, "title", "") or "", "verification": getattr(c, "verification", "") or ""}
    return {**{k: core[k] for k in ("id", "id_type", "source", "doi")}, "hash": hash_json(core)}


def _per_source_queries(corpus) -> dict:
    out = {}
    for src in (corpus.sources_executed or []):
        try:
            out[src] = corpus.cfg.source_query(src)
        except Exception:
            out[src] = ""
    return out


def search_manifest(corpus, *, timestamp: str) -> dict:
    """Freeze the returned record set (typed + per-record hashes) so reproduction
    replays it instead of re-querying, and can detect metadata drift."""
    key = lambda r: (r["source"], r["id_type"], r["id"])
    identified = sorted((_record_entry(c) for c in corpus.identified), key=key)
    verified = sorted((_record_entry(c) for c in corpus.verified), key=key)
    manifest = {
        "timestamp_utc": timestamp,
        "query": getattr(corpus.cfg, "pubmed_query", ""),
        "per_source": dict(corpus.per_source or {}),
        "per_source_query": _per_source_queries(corpus),
        "sources_executed": list(corpus.sources_executed or []),
        "identified": identified,
        "verified": verified,
        "counts": {"identified": len(identified), "verified": len(verified),
                   "rejected": len(corpus.rejected)},
        "retraction_check": "deterministic publication-type only (live Retraction-Watch/Crossref check: future)",
    }
    manifest["frozen_set_hash"] = hash_json({"identified": identified, "verified": verified})
    return manifest


def write_search_manifest(run_dir: Path, corpus, *, timestamp: str) -> Path:
    path, _ = write_json(run_dir, "search_manifest.json",
                         search_manifest(corpus, timestamp=timestamp))
    return path


def write_provenance(run_dir: Path, *, stage_log: dict, run_state, extras: dict | None = None,
                     reproducibility: dict | None = None, timestamp: str) -> Path:
    """Assemble provenance.json from the per-stage state + stage models + extras.

    Keys are never present (only model/provider/version are recorded via stage_log)."""
    stages = {}
    for name, ss in run_state.stages.items():
        stages[name] = {
            "status": ss.status,
            "input_hashes": ss.input_hashes,
            "output_hashes": ss.output_hashes,
            "params_hash": ss.params_hash,
            "gate_record_id": ss.gate_record_id,
            "reproducibility": (reproducibility or {}).get(name, "deterministic"),
        }
    prov = {
        "timestamp_utc": timestamp,
        "run_id": run_state.run_id,
        "profile": run_state.profile,
        "stage_models": stage_log,
        "stages": stages,
        **(extras or {}),
    }
    path, _ = write_json(run_dir, "provenance.json", prov)
    return path
