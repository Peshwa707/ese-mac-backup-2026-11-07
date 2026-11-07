#!/usr/bin/env python3
"""Build a static, human-reviewable queue from paired CLI screening outputs.

This package is deliberately limited to screening adjudication. It does not
write human decisions, clinical sign-off, synthesis claims, or an export gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    run_dir = args.run_dir
    out = run_dir / "human_review_package"
    out.mkdir(exist_ok=True)

    records = {str(x["id"]): x for x in load(run_dir / "to_screen.json")}
    r1 = {str(x["pmid"]): x for x in load(run_dir / "screening_r1.json")}
    r2 = {str(x["pmid"]): x for x in load(run_dir / "screening_r2.json")}
    consensus = {str(x["pmid"]): x for x in load(run_dir / "screening.json")}
    recon = load(run_dir / "reconcile.json")

    rows = []
    for pmid, draft in consensus.items():
        a, b = r1[pmid], r2[pmid]
        conflict = a["decision"] != b["decision"]
        uncertain = draft["decision"] == "uncertain"
        if not (conflict or uncertain):
            continue
        source = records.get(pmid, {})
        rows.append({
            "priority": "conflict" if conflict else "uncertain_agreement",
            "pmid": pmid,
            "title": source.get("title", ""),
            "year": source.get("year", ""),
            "source": source.get("source", ""),
            "study_type": source.get("study_type", ""),
            "ai1_decision": a["decision"],
            "ai1_confidence": a.get("confidence", ""),
            "ai1_stream": a.get("evidence_stream", ""),
            "ai1_reason": a.get("reason", ""),
            "ai2_decision": b["decision"],
            "ai2_confidence": b.get("confidence", ""),
            "ai2_stream": b.get("evidence_stream", ""),
            "ai2_reason": b.get("reason", ""),
            "draft_decision": draft["decision"],
            "draft_stream": draft.get("evidence_stream", ""),
            "draft_reason": draft.get("reason", ""),
            "human_decision": "pending",
            "human_rationale": "",
            "clinical_signoff": "pending",
        })

    rows.sort(key=lambda x: (0 if x["priority"] == "conflict" else 1, x["pmid"]))
    fields = list(rows[0]) if rows else []
    with (out / "human_review_queue.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    adjudication_fields = [
        "pmid", "human_decision", "human_rationale", "exclusion_reason",
        "evidence_stream", "reviewer_name", "reviewer_qualification",
        "reviewed_at_utc", "source_checked",
    ]
    with (out / "human_adjudication_template.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=adjudication_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "pmid": row["pmid"], "human_decision": "pending",
                "human_rationale": "", "exclusion_reason": "",
                "evidence_stream": "", "reviewer_name": "",
                "reviewer_qualification": "", "reviewed_at_utc": "",
                "source_checked": "no",
            })

    counts = {
        "records_screened": len(consensus),
        "queue_records": len(rows),
        "conflicts": sum(x["priority"] == "conflict" for x in rows),
        "uncertain_agreements": sum(x["priority"] == "uncertain_agreement" for x in rows),
        "draft_decisions": {
            k: sum(x.get("decision") == k for x in consensus.values())
            for k in ("include", "exclude", "uncertain")
        },
        "ai_reviewer_1": "codex_cli_subscription",
        "ai_reviewer_2": "claude_cli_subscription",
        "human_reviewer_2": "pending",
        "clinical_signoff": "pending",
        "screen_stage": "awaiting_approval",
        "agreement": {
            "kappa_nominal": recon.get("kappa_nominal"),
            "kappa_weighted": recon.get("kappa_weighted"),
            "metric": "llm_llm_agreement",
            "note": "AI-to-AI agreement is not human inter-rater reliability.",
        },
    }
    source_names = ["to_screen.json", "screening_r1.json", "screening_r2.json", "screening.json", "reconcile.json"]
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_dir.name,
        "status": "draft_pending_human_review",
        "source_artifacts": {name: sha256(run_dir / name) for name in source_names},
        "outputs": ["human_review_queue.csv", "human_adjudication_template.csv", "human_review_summary.json", "human_review_dashboard.html", "HUMAN_REVIEWER_2_INSTRUCTIONS.md", "CLINICAL_SIGNOFF_TEMPLATE.md"],
        "gates": {"human_reviewer_2": "pending", "clinical_signoff": "pending"},
    }
    (out / "human_review_summary.json").write_text(json.dumps({**counts, "manifest": manifest}, indent=2), encoding="utf-8")
    (out / "human_review_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape("DSM-5 neurodevelopmental review — human screening queue")
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light; font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
body {{ margin:0; background:#f5f7fb; color:#172033; }}
header {{ background:#172033; color:#fff; padding:1.1rem max(1rem,4vw); }}
main {{ max-width:1500px; margin:auto; padding:1rem max(1rem,4vw) 3rem; }}
.warning {{ background:#fff4d6; border-left:5px solid #d18b00; padding:1rem; margin:1rem 0; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.75rem; }}
.card {{ background:#fff; border-radius:12px; padding:1rem; box-shadow:0 1px 4px #17203322; }}
.num {{ display:block; font-size:1.7rem; font-weight:700; }}
.controls {{ display:flex; flex-wrap:wrap; gap:.75rem; align-items:center; margin:1rem 0; }}
input,select {{ font:inherit; padding:.65rem; border:1px solid #b7c0d1; border-radius:8px; background:#fff; }}
.table-wrap {{ overflow:auto; background:#fff; border-radius:12px; box-shadow:0 1px 4px #17203322; }}
table {{ border-collapse:collapse; width:100%; min-width:1100px; }}
th,td {{ text-align:left; vertical-align:top; padding:.7rem; border-bottom:1px solid #e5e9f0; }}
th {{ position:sticky; top:0; background:#e9eef8; }}
.conflict {{ background:#fff7f7; }}
.tag {{ display:inline-block; border-radius:999px; padding:.2rem .5rem; font-size:.8rem; font-weight:650; }}
.tag.conflict {{ color:#9a1d1d; background:#ffdede; }}
.tag.uncertain {{ color:#735000; background:#ffedb5; }}
.reason {{ max-width:360px; line-height:1.35; }}
small {{ color:#526078; }}
</style></head><body>
<header><h1>Human screening queue</h1><div>DSM-5 neurodevelopmental baseline · draft only · human gates pending</div></header>
<main>
<div class="warning"><strong>Review boundary:</strong> these are AI screening drafts. Record human Reviewer 2 decisions and rationales separately. Clinical sign-off remains a separate gate. Do not treat this page as a diagnosis, treatment recommendation, or final evidence conclusion.</div>
<section class="cards" aria-label="Queue summary">
<div class="card"><small>Records screened</small><span class="num">{counts['records_screened']}</span></div>
<div class="card"><small>Priority conflicts</small><span class="num">{counts['conflicts']}</span></div>
<div class="card"><small>Uncertain agreements</small><span class="num">{counts['uncertain_agreements']}</span></div>
<div class="card"><small>Human Reviewer 2</small><span class="num">Pending</span></div>
<div class="card"><small>Clinical sign-off</small><span class="num">Pending</span></div>
</section>
<div class="controls"><label>Search <input id="q" type="search" placeholder="PMID or title"></label>
<label>Priority <select id="priority"><option value="all">All queued</option><option value="conflict">Conflicts first</option><option value="uncertain_agreement">Uncertain agreement</option></select></label>
<label>AI stream <select id="stream"><option value="all">All streams</option><option>criterion_validation</option><option>overlap</option><option>mechanism</option><option>intervention</option><option value="blank">Blank / needs coding</option></select></label>
<span id="shown" aria-live="polite"></span></div>
<div class="table-wrap"><table><thead><tr><th>Priority</th><th>Record</th><th>Codex</th><th>Claude</th><th>Draft</th><th>Human action</th></tr></thead><tbody id="rows"></tbody></table></div>
<p><small>Source files: <code>screening_r1.json</code>, <code>screening_r2.json</code>, <code>screening.json</code>, and <code>reconcile.json</code>. Queue is bound by <code>human_review_manifest.json</code>. <a href="human_review_queue.csv">Download CSV queue</a> · <a href="human_review_summary.json">Summary JSON</a>.</small></p>
</main><script>
const data={payload};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const q=document.querySelector('#q'), priority=document.querySelector('#priority'), stream=document.querySelector('#stream'), body=document.querySelector('#rows'), shown=document.querySelector('#shown');
function render(){{const term=q.value.toLowerCase().trim(), p=priority.value, s=stream.value;
 const filtered=data.filter(x=>(p==='all'||x.priority===p)&&(s==='all'||(s==='blank'?!x.ai1_stream&&!x.ai2_stream:(x.ai1_stream===s||x.ai2_stream===s)))&&(!term||(x.pmid+' '+x.title).toLowerCase().includes(term)));
 shown.textContent=`Showing ${{filtered.length}} of ${{data.length}} queued records`;
 body.innerHTML=filtered.map(x=>`<tr class="${{x.priority==='conflict'?'conflict':''}}"><td><span class="tag ${{x.priority==='conflict'?'conflict':'uncertain'}}">${{esc(x.priority)}}</span></td><td><strong>${{esc(x.pmid)}}</strong><br><span class="reason">${{esc(x.title)}}</span><br><small>${{esc(x.year)}} · ${{esc(x.study_type)}}</small></td><td><strong>${{esc(x.ai1_decision)}}</strong> <small>${{esc(x.ai1_confidence)}} · ${{esc(x.ai1_stream||'stream blank')}}</small><div class="reason">${{esc(x.ai1_reason)}}</div></td><td><strong>${{esc(x.ai2_decision)}}</strong> <small>${{esc(x.ai2_confidence)}} · ${{esc(x.ai2_stream||'stream blank')}}</small><div class="reason">${{esc(x.ai2_reason)}}</div></td><td><strong>${{esc(x.draft_decision)}}</strong><br><small>${{esc(x.draft_stream||'stream blank')}}</small><div class="reason">${{esc(x.draft_reason)}}</div></td><td><strong>Human decision: pending</strong><br><small>Enter rationale in the adjudication record. Clinical sign-off is separate.</small></td></tr>`).join('');}}
[q,priority,stream].forEach(x=>x.addEventListener('input',render)); render();
</script></body></html>'''
    (out / "human_review_dashboard.html").write_text(page, encoding="utf-8")
    print(json.dumps({"status": "draft_pending_human_review", "output_dir": str(out), **counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
