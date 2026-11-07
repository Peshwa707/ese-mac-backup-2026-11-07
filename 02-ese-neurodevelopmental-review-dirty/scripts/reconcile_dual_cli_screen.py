"""Reconcile completed Codex/Claude CLI screening batches.

Fails closed until every batch has two complete, schema-valid outputs.  It then
writes the two reviewer artifacts, an uncertain-on-conflict consensus, and a
reconciliation record whose human/clinical gates remain pending.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import Citation
from core.sr.models import ScreenDecision, ScreeningResult
from core.sr.reconcile import reconcile


def _load_status(run_dir: Path) -> dict[int, dict]:
    path = run_dir / "screening_cli_status.jsonl"
    if not path.exists():
        raise RuntimeError("screening_cli_status.jsonl is missing")
    out: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[int(row["batch"])] = row
    return out


def _load_batch(path: Path, expected: list[str]) -> dict[str, ScreeningResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"{path} is not a JSON list")
    got: dict[str, ScreeningResult] = {}
    for item in data:
        result = ScreeningResult.model_validate(item)
        if result.pmid not in expected or result.pmid in got:
            raise RuntimeError(f"{path} contains unexpected/duplicate PMID {result.pmid}")
        got[result.pmid] = result
    missing = [p for p in expected if p not in got]
    if missing:
        raise RuntimeError(f"{path} is missing {len(missing)} expected PMIDs")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    records = [Citation.model_validate(x) for x in json.loads((run_dir / "to_screen.json").read_text())]
    ids = [c.id for c in records]
    status = _load_status(run_dir)
    n_batches = (len(ids) + 29) // 30
    missing = [i for i in range(n_batches)
               if i not in status or status[i].get("reviewers", {}).get("codex", {}).get("status") != "complete"
               or status[i].get("reviewers", {}).get("claude", {}).get("status") != "complete"]
    if missing:
        print(f"BLOCKED: {len(missing)} batches lack two complete CLI outputs: {missing[:20]}", file=sys.stderr)
        return 2

    by_role: dict[str, dict[str, ScreeningResult]] = {"codex": {}, "claude": {}}
    for batch in range(n_batches):
        start, end = batch * 30, min((batch + 1) * 30, len(ids))
        expected = ids[start:end]
        for role in by_role:
            path = run_dir / f"screening_{role}_cli" / f"batch_{batch:03d}.json"
            by_role[role].update(_load_batch(path, expected))
    if set(by_role["codex"]) != set(ids) or set(by_role["claude"]) != set(ids):
        raise RuntimeError("complete status does not cover the full expected PMID set")

    r1 = [by_role["codex"][pmid] for pmid in ids]
    r2 = [by_role["claude"][pmid] for pmid in ids]
    (run_dir / "screening_r1.json").write_text(json.dumps([x.model_dump(mode="json") for x in r1], indent=2), encoding="utf-8")
    (run_dir / "screening_r2.json").write_text(json.dumps([x.model_dump(mode="json") for x in r2], indent=2), encoding="utf-8")

    consensus: list[dict] = []
    conflicts: list[str] = []
    for a, b in zip(r1, r2):
        stream = a.evidence_stream or b.evidence_stream
        if a.decision == b.decision:
            decision = a.decision
            reason = "Codex and Claude agree; draft pending human review."
            confidence = min(a.confidence, b.confidence)
        else:
            decision = ScreenDecision.UNCERTAIN
            reason = f"Codex/Claude conflict ({a.decision.value}/{b.decision.value}); human adjudication required."
            confidence = 0.0
            conflicts.append(a.pmid)
        consensus.append(ScreeningResult(pmid=a.pmid, decision=decision, reason=reason,
                                         confidence=confidence, modality=a.modality or b.modality,
                                         evidence_stream=stream, study_design=a.study_design or b.study_design).model_dump(mode="json"))
    rec = reconcile({x.pmid: x.decision.value for x in r1}, {x.pmid: x.decision.value for x in r2})
    rec.update({
        "reviewers": {
            "ai_reviewer_1": "codex_cli_subscription",
            "ai_reviewer_2": "claude_cli_subscription",
            "human_reviewer_2": "pending",
            "clinical_signoff": "pending",
        },
        "metric": "llm_llm_agreement",
        "metric_note": "Agreement between Codex and Claude CLI reviewers; not human inter-rater reliability.",
        "conflicts": conflicts,
    })
    (run_dir / "screening.json").write_text(json.dumps(consensus, indent=2), encoding="utf-8")
    (run_dir / "reconcile.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(ids), "conflicts": len(conflicts),
                      "kappa_nominal": rec["kappa_nominal"], "status": "draft_pending_human"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
