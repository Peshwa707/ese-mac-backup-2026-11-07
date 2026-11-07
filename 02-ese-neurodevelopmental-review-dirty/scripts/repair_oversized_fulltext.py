"""Create a provenance-preserving prompt-side repair for noisy OA HTML.

The governed full-text artifact is never overwritten. For an oversized
Unpaywall landing-page extraction, this fetches the same legal OA location via
the existing connector, uses its HTML parser to remove script/style chrome,
and records a sidecar path/hash in ``fulltext_records.json``. Batch identity
and provider setup remain unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.connectors.pmc import PmcConnector
from core.connectors.unpaywall import UnpaywallConnector


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def fetch_clean(pmid: str) -> str:
    pmc = PmcConnector()
    uw = UnpaywallConnector()
    try:
        doi = await pmc.doi_for(pmid)
        if not doi:
            raise RuntimeError(f"No DOI resolved for PMID {pmid}")
        text = await uw.fulltext(doi)
        if not text:
            raise RuntimeError(f"No legal Unpaywall text resolved for DOI {doi}")
        return text
    finally:
        await pmc.aclose()
        await uw.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--pmid", required=True)
    args = parser.parse_args()

    out = args.run_dir / "ai_fulltext_draft"
    records_path = out / "fulltext_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    record = next((x for x in records if str(x.get("pmid")) == args.pmid), None)
    if record is None:
        raise SystemExit(f"PMID {args.pmid} not found in fulltext_records.json")
    if record.get("source") != "unpaywall":
        raise SystemExit("Prompt-side HTML normalization is limited to Unpaywall records")

    text = asyncio.run(fetch_clean(args.pmid))
    sidecar_dir = out / "prompt_fulltexts"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar = sidecar_dir / f"{args.pmid}.txt"
    sidecar.write_text(text, encoding="utf-8")

    backup = out / "fulltext_records.json.pre_prompt_normalization"
    if not backup.exists():
        backup.write_text(records_path.read_text(encoding="utf-8"), encoding="utf-8")
    record["prompt_full_text_file"] = str(sidecar)
    record["prompt_full_text_sha256"] = sha256(text)
    record["prompt_normalization"] = "Unpaywall HTML parser removed script/style/noscript/template/svg/head page chrome; original artifact retained"
    record["prompt_chars"] = len(text)
    records_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "pmid": args.pmid,
        "original_chars": record.get("chars"),
        "prompt_chars": len(text),
        "sidecar": str(sidecar),
        "original_sha256": record.get("full_text_sha256"),
        "prompt_sha256": record["prompt_full_text_sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
