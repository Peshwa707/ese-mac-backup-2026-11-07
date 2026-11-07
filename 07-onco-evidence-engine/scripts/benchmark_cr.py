"""Headline acceptance number: does LOCAL extraction reproduce the pooled CR ~54%?

Local-extracts complete_response_rate + total_n on the known included studies, pools
via R metaprop (Freeman-Tukey), and compares to the published 54%. Isolates local
*extraction* fidelity from screening (screening fidelity is benchmarked separately).
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from core.models import Citation, IdType
from core.llm import client
from core.sr.config import load_sr_config
from core.sr.extract import build_extraction_prompt
from core.sr.models import ExtractedStudy
from core.sr.stats.meta import r_available, run_metaprop
from core.sr.stats.prepare import outcome_rows

RUN = Path("runs/mm-bcma-3l")


def _known_included_pmids() -> set[str]:
    pmids = set()
    for f in glob.glob(str(RUN / "extract_idx_*.json")):
        d = json.load(open(f))
        for s in (d if isinstance(d, list) else d.get("results", [d])):
            if isinstance(s, dict) and s.get("pmid"):
                pmids.add(str(s["pmid"]))
    return pmids


def _citation(rec):
    return Citation(id_type=IdType.PMID, id=rec["id"], source="pubmed",
                    title=rec.get("title", ""), snippet=rec.get("snippet", ""),
                    study_type=rec.get("study_type", ""), year=rec.get("year"), verified=True)


def main():
    cfg = load_sr_config("mm-bcma-3l")
    by_id = {r["id"]: r for r in json.load(open(RUN / "corpus.json"))["verified"]}
    known = sorted(_known_included_pmids() & set(by_id))
    print(f"model={client.OLLAMA_MODEL} extracting CR on {len(known)} known includes", file=sys.stderr)

    studies, fails = [], 0
    for i, pmid in enumerate(known):
        system, user = build_extraction_prompt(_citation(by_id[pmid]), cfg)
        try:
            es = client._ollama_parse(ExtractedStudy, system, user, max_tokens=1600)
            es.pmid = pmid
            studies.append(es)
        except Exception:
            fails += 1
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(known)} extracted ({fails} fail)", file=sys.stderr)

    rows, excluded = outcome_rows(studies, "complete_response_rate", {})
    out = {"model": client.OLLAMA_MODEL, "known_includes": len(known),
           "extract_fail": fails, "cr_poolable_k": len(rows), "cr_excluded": excluded,
           "published_cr": 54.0}
    if r_available() and len(rows) >= 2:
        res = run_metaprop(rows, "Complete response rate (CR)", RUN / "bench_cr_forest.png")
        overall = next((r for r in res if r.group == "ALL"), res[0] if res else None)
        if overall:
            out["pooled_cr_local"] = round(overall.prop, 1)
            out["ci"] = [round(overall.ci_lb, 1), round(overall.ci_ub, 1)]
            out["I2"] = round(overall.I2, 0)
            out["delta_vs_published"] = round(overall.prop - 54.0, 1)
    slug = client.OLLAMA_MODEL.replace(":", "_").replace("/", "_")
    (RUN / f"benchmark_cr_{slug}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
