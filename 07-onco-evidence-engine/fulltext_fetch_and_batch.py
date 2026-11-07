"""Fetch full-text content for the 567 candidates, save per-PMID, build FT screening batches."""
import json, pathlib, asyncio
from core.sr.fulltext import resolve_many
RUN = pathlib.Path("runs/mm-bcma-early-relapse")
CONTENT = RUN / "fulltext_content"; CONTENT.mkdir(exist_ok=True)
recs = {str(r["pmid"]): r for r in json.load(open(RUN / "records_for_agents.json"))}
cands = [str(p) for p in json.load(open(RUN / "final_advance_v2.json")) if str(p).isdigit()]


async def main():
    res = await resolve_many(cands, concurrency=6)
    ft_records, ab_records = [], []
    for r in res:
        rec = recs.get(r.pmid, {})
        base = {"pmid": r.pmid, "title": rec.get("title", ""), "year": rec.get("year", "")}
        if r.available and r.chars >= 500:
            # cap stored text to keep agent prompts manageable (methods+results usually early)
            text = r.text[:24000]
            (CONTENT / f"{r.pmid}.txt").write_text(text, encoding="utf-8")
            ft_records.append({**base, "source": r.source, "has_fulltext": True})
        else:
            (CONTENT / f"{r.pmid}.txt").write_text(
                "[NO FULL TEXT — abstract only]\n\n" + (rec.get("abstract", "") or ""),
                encoding="utf-8")
            ab_records.append({**base, "source": "abstract_only", "has_fulltext": False})

    # batches: full-text 8/agent (big papers), abstract-only 30/agent
    ftdir = RUN / "ft_screen_in"; ftdir.mkdir(exist_ok=True)
    n = 0
    for i in range(0, len(ft_records), 8):
        (ftdir / f"ft_{i//8:03d}.json").write_text(json.dumps(ft_records[i:i+8], indent=2)); n += 1
    for i in range(0, len(ab_records), 30):
        (ftdir / f"ab_{i//30:03d}.json").write_text(json.dumps(ab_records[i:i+30], indent=2)); n += 1
    (RUN / "ft_screen_out").mkdir(exist_ok=True)
    print(f"full-text content saved: {len(ft_records)} with FT, {len(ab_records)} abstract-only")
    print(f"FT batches (8/ea): {(len(ft_records)+7)//8} | abstract batches (30/ea): {(len(ab_records)+29)//30} | total {n}")


asyncio.run(main())
