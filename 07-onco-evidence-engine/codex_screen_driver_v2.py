"""Codex = Reviewer 2: screen every batch via `codex exec` (structured JSON, resumable)."""
import json, subprocess, pathlib, sys, time

RUN = pathlib.Path("runs/mm-bcma-early-relapse")
CRITERIA = (RUN / "screening_prompt.txt").read_text()
SCHEMA = str(RUN / "codex_screen_schema.json")
IN = RUN / "codex_in"
OUT = RUN / "codex_out_v2"; OUT.mkdir(exist_ok=True)
LOG = RUN / "codex_progress_v2.log"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def screen_batch(records):
    prompt = (CRITERIA + "\n\n## RECORDS TO SCREEN (decide each by title/abstract ONLY)\n"
              + json.dumps(records, indent=2)
              + '\n\nReturn a JSON object with a "decisions" array: one entry per record '
                "(pmid, decision in include/exclude/uncertain, reason<=15 words). "
                "Use no outside knowledge; if abstract insufficient -> uncertain.")
    proc = subprocess.run(
        ["codex", "exec", "-s", "read-only", "--skip-git-repo-check",
         "--output-schema", SCHEMA, prompt],
        capture_output=True, text=True, timeout=600,
    )
    out = proc.stdout.strip()
    start = out.find("{")
    if start == -1:
        raise ValueError(f"no JSON in codex output: {out[:200]} / err: {proc.stderr[:200]}")
    return json.loads(out[start:])["decisions"]


def main():
    batches = sorted(IN.glob("batch_*.json"))
    log(f"Codex R2 starting: {len(batches)} batches")
    for bf in batches:
        of = OUT / bf.name
        if of.exists():
            try:
                json.loads(of.read_text()); log(f"skip {bf.name} (done)"); continue
            except Exception:
                pass
        records = json.loads(bf.read_text())
        for attempt in (1, 2):
            try:
                decisions = screen_batch(records)
                of.write_text(json.dumps(decisions, indent=2))
                log(f"OK   {bf.name}: {len(decisions)} decisions")
                break
            except Exception as e:
                log(f"ERR  {bf.name} attempt {attempt}: {str(e)[:160]}")
                if attempt == 2:
                    of.with_suffix(".error").write_text(str(e))
    # merge
    merged = {}
    for of in sorted(OUT.glob("batch_*.json")):
        for d in json.loads(of.read_text()):
            merged[str(d["pmid"])] = d["decision"]
    (RUN / "codex_r2_v2_all.json").write_text(json.dumps(merged, indent=2))
    log(f"Codex R2 COMPLETE: {len(merged)} records -> codex_r2_v2_all.json")


if __name__ == "__main__":
    main()
