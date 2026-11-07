"""Aggregate benchmark score reports."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.models import ScoreReport


def _score_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(path.glob("*.json")))
        else:
            out.append(path)
    return out


def aggregate_scores(score_paths: list[Path], out_json: Path, out_md: Path) -> dict:
    reports = [ScoreReport.model_validate_json(p.read_text(encoding="utf-8")) for p in _score_files(score_paths)]
    counts: dict[str, int] = {}
    headline_counts = {"pass": 0, "fail": 0, "not_scored": 0}
    key_sealed_counts = {"sealed": 0, "draft": 0}
    conclusion_counts = {"matched": 0, "mismatched": 0, "not_scored": 0}
    integrity_totals = {"estimated_value_count": 0, "nr_value_count": 0, "pooled_n_excluded_total": 0}
    caveats: list[str] = []
    for report in reports:
        key_sealed_counts["sealed" if report.key_sealed else "draft"] += 1
        for cls, n in report.counts_by_classification.items():
            counts[cls] = counts.get(cls, 0) + n
        for outcome in report.outcomes:
            if outcome.headline_pass is True:
                headline_counts["pass"] += 1
            elif outcome.headline_pass is False:
                headline_counts["fail"] += 1
            else:
                headline_counts["not_scored"] += 1
        if report.conclusion.matched is True:
            conclusion_counts["matched"] += 1
        elif report.conclusion.matched is False:
            conclusion_counts["mismatched"] += 1
        else:
            conclusion_counts["not_scored"] += 1
        integrity_totals["estimated_value_count"] += report.integrity.estimated_value_count
        integrity_totals["nr_value_count"] += report.integrity.nr_value_count
        integrity_totals["pooled_n_excluded_total"] += report.integrity.pooled_n_excluded_total
        caveats.extend(f"{report.case_id}: {c}" for c in report.caveats)
    summary = {
        "n_cases": len(reports),
        "outcome_counts": counts,
        "headline_counts": headline_counts,
        "key_sealed_counts": key_sealed_counts,
        "conclusion_counts": conclusion_counts,
        "integrity_totals": integrity_totals,
        "caveats": caveats,
        "cases": [r.model_dump(mode="json") for r in reports],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = [
        "# Benchmark Summary",
        "",
        f"Cases: {len(reports)}",
        "",
        "## Outcome Classifications",
        "",
    ]
    for key in ("matched", "mismatched", "missing_engine", "missing_key", "not_scored"):
        lines.append(f"- {key}: {counts.get(key, 0)}")
    lines += ["", "## Headline Concordance", ""]
    for key in ("pass", "fail", "not_scored"):
        lines.append(f"- {key}: {headline_counts.get(key, 0)}")
    lines += ["", "## Key Status", ""]
    lines.append(f"- sealed: {key_sealed_counts['sealed']}")
    lines.append(f"- draft: {key_sealed_counts['draft']}")
    lines += ["", "## Integrity Diagnostics", ""]
    for key, value in integrity_totals.items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Conclusion Concordance", ""]
    for key in ("matched", "mismatched", "not_scored"):
        lines.append(f"- {key}: {conclusion_counts.get(key, 0)}")
    lines += ["", "## Caveats", ""]
    lines.extend(f"- {c}" for c in caveats) if caveats else lines.append("- None")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
