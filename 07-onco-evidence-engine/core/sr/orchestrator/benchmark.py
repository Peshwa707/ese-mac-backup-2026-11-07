"""Benchmark / validation mode (WP-M4.4, spec §8).

Neutralizes the "graded their own homework" critique with THREE enforced controls:
  1. Pre-registration binding: start requires a frozen protocol (protocol_freeze.json,
     from promote_protocol) whose hash matches, AND that search has not yet run — so a
     protocol can't be tuned to results and then back-dated.
  2. Sealed gold: the published gold standard must live OUTSIDE the run dir; only a hash
     commitment is stored, so the orchestrator (which reads only run-dir artifacts) cannot
     see the answer mid-run.
  3. Post-gate reveal: benchmark_compare fails closed unless the run's honesty gates are
     cleared — the gold can't be opened during screening/extraction iteration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.sr.orchestrator.adapters import read_json, write_json
from core.sr.orchestrator.hashing import hash_file


class BenchmarkError(RuntimeError):
    """A benchmark precondition (pre-registration, sealing, gate-clearance, integrity) failed."""


def start_benchmark(run_dir: Path, *, protocol_hash: str, gold_path: Path,
                    blind: bool = True) -> dict:
    run_dir = Path(run_dir)
    # 1. pre-registration: a frozen protocol must exist and match, BEFORE any search/stage.
    freeze_p = run_dir / "protocol_freeze.json"
    if not freeze_p.exists():
        raise BenchmarkError("benchmark requires a frozen pre-registered protocol "
                             "(run promote_protocol first)")
    freeze = json.loads(freeze_p.read_text(encoding="utf-8"))
    if not protocol_hash or protocol_hash != freeze.get("freeze_hash"):
        raise BenchmarkError("protocol_hash must match the frozen protocol's freeze_hash")
    if (run_dir / "corpus.json").exists():
        raise BenchmarkError("search has already run — a benchmark must be registered BEFORE "
                             "search (no post-hoc protocol tuning)")
    rs = run_dir / "run_state.json"
    if rs.exists():
        from core.sr.orchestrator import state as st
        try:
            state = st.load(run_dir)
        except st.StateCorruption as e:
            # untrustworthy state may hide already-run stages -> fail closed, don't proceed
            raise BenchmarkError("existing run_state.json is corrupt — cannot register a "
                                 "benchmark on untrustworthy state") from e
        # ANY non-pending stage means a stage already executed (incl. awaiting_approval/failed).
        if any(s.status != "pending" for s in state.stages.values()):
            raise BenchmarkError("a stage has already executed — register the benchmark before "
                                 "any stage runs")

    # 2. seal: the gold standard must live OUTSIDE the run dir (run API can't reach it).
    if not Path(gold_path).exists():
        raise FileNotFoundError(f"gold standard not found: {gold_path}")
    rd_abs = os.path.abspath(str(run_dir))
    # check the SUPPLIED path (symlinks NOT followed) so an in-run symlink to an external
    # file is still rejected — the symlink itself would be readable from the run dir.
    supplied_abs = os.path.abspath(str(gold_path))
    if supplied_abs == rd_abs or supplied_abs.startswith(rd_abs + os.sep):
        raise BenchmarkError("gold standard must be sealed OUTSIDE the run directory")
    # belt-and-suspenders: the resolved target must also be outside
    gp = Path(gold_path).resolve()
    rd_real = Path(run_dir).resolve()
    if gp == rd_real or rd_real in gp.parents:
        raise BenchmarkError("gold standard must be sealed OUTSIDE the run directory")

    manifest = {
        "protocol_hash": protocol_hash,
        "blind": blind,
        "gold_sealed": True,
        "gold_hash": hash_file(gp),       # commitment only — gold content is NEVER in the run dir
    }
    write_json(run_dir, "benchmark.json", manifest)
    return manifest


def gold_readable_from_run_dir(run_dir: Path) -> bool:
    """True if any gold CONTENT is reachable by reading run-dir artifacts (should be False)."""
    bench = read_json(run_dir, "benchmark.json")
    return any(k in bench for k in ("included_pmids", "pooled_estimate", "gold"))


def _gates_cleared(run_dir: Path) -> bool:
    """Whether the run's honesty gates are cleared (export-allowed). Self-verifying so the
    gold can't be revealed mid-run."""
    from core.sr.orchestrator import engine, gates
    from core.sr.orchestrator import state as st
    try:
        state = st.load(run_dir)
    except Exception:
        return False
    ok, _ = gates.export_allowed(run_dir, state, engine.build_registry())
    return ok


def _prf(ours: set[str], gold: set[str]) -> dict:
    tp, fp, fn = len(ours & gold), len(ours - gold), len(gold - ours)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4)}


def benchmark_compare(run_dir: Path, gold_path: Path, *, our_included: list[str],
                      our_estimate: float | None = None,
                      our_ci: tuple[float, float] | None = None) -> dict:
    """Open the sealed gold AFTER the run and compute pre-specified metrics. Fails closed
    unless honesty gates are cleared, and verifies the gold matches the sealed commitment."""
    if not _gates_cleared(run_dir):
        raise BenchmarkError("honesty gates not cleared — refusing to reveal the gold "
                             "standard before human sign-off")
    bench = read_json(run_dir, "benchmark.json")
    gp = Path(gold_path)
    if hash_file(gp) != bench.get("gold_hash"):
        raise BenchmarkError("gold standard does not match the sealed commitment (tampered/swapped)")
    gold = json.loads(gp.read_text(encoding="utf-8"))

    result = {"protocol_hash": bench["protocol_hash"], "blind": bench.get("blind", True),
              "included": _prf(set(map(str, our_included)),
                               set(map(str, gold.get("included_pmids", []))))}
    g_est = gold.get("pooled_estimate")
    if our_estimate is not None and g_est is not None:    # 0.0 is a valid estimate, not "absent"
        result["estimate"] = {"ours": our_estimate, "published": g_est,
                              "ratio": round(our_estimate / g_est, 4) if g_est else None}
        if our_ci and gold.get("ci_low") is not None and gold.get("ci_high") is not None:
            lo, hi = sorted(our_ci)                        # tolerate reversed bounds
            glo, ghi = sorted((gold["ci_low"], gold["ci_high"]))
            import math as _m
            if not all(_m.isfinite(v) for v in (lo, hi, glo, ghi, g_est)):
                raise BenchmarkError("non-finite CI/estimate in benchmark comparison")
            result["estimate"]["published_point_in_our_ci"] = bool(lo <= g_est <= hi)
            result["estimate"]["ci_overlap"] = bool(lo <= ghi and glo <= hi)
    return result
