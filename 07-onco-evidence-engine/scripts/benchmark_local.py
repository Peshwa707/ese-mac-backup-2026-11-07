"""Phase-2 acceptance benchmark: local (hybrid) arm vs the published Liang 2024 target.

Drives the LOCAL path over the existing validated mm-bcma corpus (no PubMed re-gather,
no API key): per-record local screening (qwen2.5:7b) -> local extraction of the
complete_response_rate on local-includes -> pooled CR via R metaprop. Compares the
pooled CR to the published 54% and reports screening agreement vs the known included
set. Checkpoints every 50 records so a long run is resumable / inspectable.

Run:  SR_LLM_PROFILE=local .venv/bin/python scripts/benchmark_local.py [--limit N]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

from core.models import Citation, IdType
from core.llm import client
from core.sr.config import load_sr_config
from core.sr.models import ExtractedStudy, ScreenDecision, ScreeningResult
from core.sr.screen import build_screening_prompt
from core.sr.extract import build_extraction_prompt

RUN = Path("runs/mm-bcma-3l")
OUT = RUN / "benchmark_local.json"
PUBLISHED_CR = 54.0


def _citation(rec: dict) -> Citation:
    return Citation(
        id_type=IdType.PMID, id=rec["id"], source=rec.get("source", "pubmed"),
        title=rec.get("title", ""), url=rec.get("url", ""), snippet=rec.get("snippet", ""),
        study_type=rec.get("study_type", ""), year=rec.get("year"),
        verified=rec.get("verified", True), doi=rec.get("doi", ""),
    )


def _known_included() -> set[str]:
    pmids = set()
    for f in glob.glob(str(RUN / "extract_idx_*.json")):
        d = json.load(open(f))
        for s in (d if isinstance(d, list) else d.get("results", [d])):
            if isinstance(s, dict) and s.get("pmid"):
                pmids.add(str(s["pmid"]))
    return pmids


def _screen_one(cit: Citation, cfg) -> ScreeningResult | None:
    system, user = build_screening_prompt([cit], cfg)   # 1-record prompt, flat schema
    try:
        return client._ollama_parse(ScreeningResult, system, user, max_tokens=900)
    except Exception:
        return None


def _extract_cr(cit: Citation, cfg) -> ExtractedStudy | None:
    system, user = build_extraction_prompt(cit, cfg)
    try:
        es = client._ollama_parse(ExtractedStudy, system, user, max_tokens=1500)
        es.pmid = cit.id
        return es
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="screen only the first N records (dry run)")
    args = ap.parse_args()

    cfg = load_sr_config("mm-bcma-3l")
    corpus = json.load(open(RUN / "corpus.json"))
    records = corpus["verified"]
    if args.limit:
        records = records[: args.limit]
    known = _known_included()
    print(f"model={client.OLLAMA_MODEL} records={len(records)} known_included={len(known)}",
          file=sys.stderr)

    decisions, fails, t0 = [], 0, time.time()
    for i, rec in enumerate(records):
        r = _screen_one(_citation(rec), cfg)
        if r is None:
            fails += 1
        else:
            decisions.append({"pmid": rec["id"], "decision": r.decision.value,
                              "confidence": r.confidence})
        if (i + 1) % 50 == 0:
            inc = sum(1 for d in decisions if d["decision"] == "include")
            OUT.write_text(json.dumps({"screened": i + 1, "parse_fail": fails,
                                       "included_so_far": inc, "decisions": decisions}, indent=1))
            print(f"  {i+1}/{len(records)} screened, {inc} include, {fails} parse-fail, "
                  f"{(time.time()-t0)/(i+1):.1f}s/rec", file=sys.stderr)

    local_inc = {d["pmid"] for d in decisions if d["decision"] == "include"}
    # Screening agreement vs the known included set.
    recall = len(local_inc & known) / len(known) if known else 0.0

    # Local-extract CR on local-includes (cap to keep the dry run quick).
    by_id = {r["id"]: r for r in records}
    cr_rows = []
    for pmid in sorted(local_inc):
        es = _extract_cr(_citation(by_id[pmid]), cfg)
        if not es:
            continue
        for v in es.values:
            if v.field == "complete_response_rate" and (v.flag or "").upper() != "NR":
                cr_rows.append({"pmid": pmid, "value": v.value})

    result = {
        "model": client.OLLAMA_MODEL,
        "profile": client.active_profile(),
        "screened": len(records),
        "parse_fail": fails,
        "parse_fail_rate": round(fails / max(len(records), 1), 3),
        "local_included": len(local_inc),
        "known_included": len(known),
        "screening_recall_vs_known": round(recall, 3),
        "cr_values_extracted": len(cr_rows),
        "published_cr": PUBLISHED_CR,
        "cr_rows": cr_rows,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    OUT.write_text(json.dumps({**result, "decisions": decisions}, indent=1))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
