"""Run the same title/abstract screening prompt through Codex and Claude CLIs.

This runner is intentionally separate from the API gateway.  It uses the local
subscription CLIs, removes API-key variables from both child environments, and
keeps raw output plus parsed decisions for every batch.  It never writes the
consensus or human adjudication artifacts; those remain downstream gates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow direct execution from the repository root without requiring an editable
# install (`python scripts/run_dual_cli_screen.py ...`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import Citation
from core.sr.config import load_sr_config
from core.sr.models import ScreeningBatch, ScreeningResult
from core.sr.screen import build_screening_prompt


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_from_text(text: str) -> Any:
    """Parse strict JSON, fenced JSON, or a CLI envelope containing JSON."""
    text = text.strip()
    candidates = [text]
    if text.startswith("```"):
        candidates.append(re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # Find the first complete JSON object/array in logs that may surround the answer.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            return decoder.raw_decode(text[match.start():])[0]
        except json.JSONDecodeError:
            continue
    raise ValueError("CLI output did not contain parseable JSON")


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("structured_output", "structuredOutput", "result", "output"):
            if key in value:
                nested = value[key]
                if isinstance(nested, str):
                    try:
                        return _unwrap(_json_from_text(nested))
                    except ValueError:
                        continue
                return _unwrap(nested)
    return value


def _validate(raw: str, expected: list[str]) -> list[dict]:
    value = _unwrap(_json_from_text(raw))
    if isinstance(value, list):
        value = {"results": value}
    batch = ScreeningBatch.model_validate(value)
    expected_set = set(expected)
    seen: set[str] = set()
    out: list[dict] = []
    for item in batch.results:
        if item.pmid not in expected_set or item.pmid in seen:
            continue
        seen.add(item.pmid)
        out.append(item.model_dump(mode="json"))
    # Missing records become explicit uncertainty, never silently disappear.
    for pmid in expected:
        if pmid not in seen:
            out.append({
                "pmid": pmid,
                "decision": "uncertain",
                "reason": "CLI reviewer returned no decision; preserved for human review.",
                "confidence": 0.0,
                "modality": "",
                "evidence_stream": "",
                "study_design": "unclear",
            })
    return out


def _strict_schema(schema: dict) -> dict:
    """Make Pydantic's schema acceptable to Codex structured-output validation."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
            if isinstance(schema.get("properties"), dict):
                # Codex's structured-output validator requires every declared
                # property to be listed as required, even when Pydantic marks it
                # optional. Defaults are still accepted in the parsed model.
                schema["required"] = list(schema["properties"])
        for value in schema.values():
            if isinstance(value, dict):
                _strict_schema(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _strict_schema(item)
    return schema


def _child_env(provider: str) -> dict[str, str]:
    env = dict(os.environ)
    # Explicitly prevent either CLI from silently switching to an API path.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY",
                "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
        env.pop(key, None)
    env["ESE_REVIEW_PROVIDER"] = provider
    return env


def _run_cli(provider: str, prompt: str, schema_path: Path, raw_path: Path,
             *, model: str = "", timeout_seconds: int = 900) -> tuple[int, str]:
    if provider == "codex":
        cmd = ["codex", "exec", "--ephemeral", "--sandbox", "read-only",
               "--skip-git-repo-check", "--color", "never",
               "--output-schema", str(schema_path), "-o", str(raw_path)]
        if model:
            cmd.extend(["--model", model])
        stdin = prompt.encode()
        proc = subprocess.run(cmd + ["-"], input=stdin, capture_output=True,
                              env=_child_env("codex_cli_subscription"),
                              timeout=timeout_seconds)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if not raw_path.exists() or not raw_path.read_text(encoding="utf-8", errors="replace").strip():
            raw_path.write_text(stdout or stderr, encoding="utf-8")
        return proc.returncode, stderr
    if provider == "claude":
        schema = json.dumps(json.loads(schema_path.read_text(encoding="utf-8")), separators=(",", ":"))
        cmd = ["claude", "-p", "--output-format", "json", "--json-schema", schema,
               "--effort", os.environ.get("CLAUDE_REVIEW_EFFORT", "max"),
               "--tools", "", "--no-session-persistence", "--permission-mode", "dontAsk"]
        if model:
            cmd.extend(["--model", model])
        proc = subprocess.run(cmd, input=prompt.encode(), capture_output=True,
                              env=_child_env("claude_cli_subscription"),
                              timeout=timeout_seconds)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raw_path.write_text(stdout or stderr, encoding="utf-8")
        return proc.returncode, stderr
    raise ValueError(provider)


async def _run_pair(batch_no: int, records: list[Citation], cfg, run_dir: Path,
                    schema_path: Path, *, retries: int, codex_model: str,
                    claude_model: str) -> dict:
    system, user = build_screening_prompt(records, cfg)
    prompt = system + "\n\n" + user
    prompt_path = run_dir / "screening_cli_prompts" / f"batch_{batch_no:03d}.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    expected = [c.id for c in records]
    result: dict[str, Any] = {"batch": batch_no, "pmids": expected, "started_at": _now(), "reviewers": {}}

    async def one(provider: str, model: str) -> tuple[str, dict]:
        out_dir = run_dir / f"screening_{provider}_cli"
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"batch_{batch_no:03d}.raw"
        parsed_path = out_dir / f"batch_{batch_no:03d}.json"
        errors: list[str] = []
        for attempt in range(1, retries + 2):
            # Never allow an earlier attempt/run to masquerade as this attempt.
            raw_path.unlink(missing_ok=True)
            parsed_path.unlink(missing_ok=True)
            try:
                rc, stderr = await asyncio.to_thread(_run_cli, provider, prompt, schema_path, raw_path,
                                                      model=model)
                raw = raw_path.read_text(encoding="utf-8", errors="replace") if raw_path.exists() else ""
                if rc != 0:
                    raise RuntimeError(f"CLI returned exit code {rc}: {stderr[-1000:]}")
                parsed = _validate(raw, expected)
                parsed_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                return provider, {"status": "complete", "attempts": attempt,
                                  "provider": f"{provider}_cli_subscription", "model": model,
                                  "raw": str(raw_path), "parsed": str(parsed_path),
                                  "returncode": rc, "stderr": stderr[-2000:]}
            except Exception as exc:
                errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt <= retries:
                    raw_path.unlink(missing_ok=True)
        return provider, {"status": "failed", "attempts": retries + 1,
                          "provider": f"{provider}_cli_subscription", "model": model,
                          "raw": str(raw_path), "errors": errors}

    pair = await asyncio.gather(one("codex", codex_model), one("claude", claude_model),
                                return_exceptions=True)
    for item in pair:
        if isinstance(item, Exception):
            provider = "codex" if "codex" not in result["reviewers"] else "claude"
            result["reviewers"][provider] = {
                "status": "failed", "provider": f"{provider}_cli_subscription",
                "errors": [f"{type(item).__name__}: {item}"],
            }
        else:
            provider, details = item
            result["reviewers"][provider] = details
    result["completed_at"] = _now()
    status_path = run_dir / "screening_cli_status.jsonl"
    with status_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, sort_keys=True) + "\n")
    return result


async def main_async(args) -> int:
    run_dir = Path(args.run_dir)
    state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))["state"]
    cfg = load_sr_config(state["vertical"])
    records = [Citation.model_validate(x) for x in json.loads((run_dir / "to_screen.json").read_text())]
    batch_size = max(1, args.batch_size)
    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    schema_path = run_dir / "screening_cli_schema.json"
    schema_path.write_text(json.dumps(_strict_schema(ScreeningBatch.model_json_schema()), indent=2), encoding="utf-8")
    completed: dict[int, dict] = {}
    status_path = run_dir / "screening_cli_status.jsonl"
    if status_path.exists():
        for line in status_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("reviewers", {}).get("codex", {}).get("status") == "complete" \
                        and row.get("reviewers", {}).get("claude", {}).get("status") == "complete":
                    completed[int(row["batch"])] = row
            except (ValueError, KeyError):
                continue
    selected = list(range(len(batches)))
    if args.start is not None:
        selected = [i for i in selected if i >= args.start]
    if args.limit is not None:
        selected = selected[:args.limit]
    for i in selected:
        if i in completed and not args.rerun:
            print(f"SKIP batch {i:03d} (both complete)", flush=True)
            continue
        print(f"RUN batch {i:03d}/{len(batches)-1:03d} ({len(batches[i])} records)", flush=True)
        row = await _run_pair(i, batches[i], cfg, run_dir, schema_path, retries=args.retries,
                              codex_model=args.codex_model, claude_model=args.claude_model)
        print(json.dumps({"batch": i, "codex": row["reviewers"]["codex"]["status"],
                          "claude": row["reviewers"]["claude"]["status"]}), flush=True)
        if any(v["status"] != "complete" for v in row["reviewers"].values()):
            print("STOP: provider failure preserved; no silent substitution", file=sys.stderr)
            return 2
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--start", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--rerun", action="store_true")
    p.add_argument("--codex-model", default=os.environ.get("CODEX_REVIEW_MODEL", ""))
    p.add_argument("--claude-model", default=os.environ.get("CLAUDE_REVIEW_MODEL", "opus"))
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
