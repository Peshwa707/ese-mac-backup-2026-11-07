#!/usr/bin/env python3
"""Prepare a blinded, same-protocol packet for Claude AI Reviewer 2.

The packet deliberately contains the frozen protocol, original user prompt,
claim ledger, module specifications, and the 980 records advanced under the
rapid-review cap. It deliberately excludes every Codex screening, extraction,
synthesis, and final-review artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "review_inputs" / "neurodevelopmental-environmental-mismatch"
SOURCE_RUN = ROOT / "runs" / "ndem-rapid-20260729"
DEFAULT_BLIND_ROOT = Path(
    "/Users/neurologic/Projects/ndem-claude-secondary-review-20260730"
)
SOURCE_PROTOCOL = Path(
    "/Users/neurologic/Downloads/"
    "neurodevelopmental_environmental_mismatch_review_protocol.md"
)
SOURCE_PROMPT = Path(
    "/Users/neurologic/.codex/attachments/"
    "29d05425-f0cb-4a23-81d7-77dca229ae4d/pasted-text.txt"
)

MODULES = {
    "ndem-a-persistence-compensation": "spec-a-persistence-compensation.json",
    "ndem-b-overlap-social-dcd": "spec-b-overlap-social-dcd.json",
    "ndem-c-context-motivation-strengths": "spec-c-context-motivation-strengths.json",
    "ndem-d-external-scaffolding": "spec-d-external-scaffolding.json",
    "ndem-e1-sleep-circadian": "spec-e1-sleep-circadian.json",
    "ndem-e2-mood-substances": "spec-e2-mood-substances.json",
    "ndem-f-daydreaming-rejection": "spec-f-daydreaming-rejection.json",
}
MODULE_FILES = (
    "screening_candidates.json",
    "corpus.json",
    "records_for_agents.json",
    "search_execution.json",
    "search_manifest.json",
    "screening_scope.json",
)

CONTRACT = """# Claude secondary AI reviewer execution contract

## Role boundary

- Role: independent secondary **AI** reviewer, implemented with Claude.
- This is not a human Reviewer 2, clinician, or treating-psychiatrist sign-off.
- The result remains an abstract-level rapid systematic evidence-review draft.

## Blinding boundary

- Claude receives the source protocol, original prompt, frozen claim ledger,
  module specifications, search manifests, and the same 980 title/abstract
  records advanced under the rapid-review cap.
- Claude receives no Codex screening decisions, extraction rows, verdicts,
  narrative synthesis, claim-evidence matrix, bibliography, or final report
  until its independent review artifacts are immutable and hashed.
- Claude runs in safe mode with no tools and no session persistence. All source
  text is supplied in the frozen prompt; it cannot inspect the Codex worktree.

## Evidence boundary

- Screening and synthesis use inspected PubMed abstracts in the frozen packet.
- A title alone is never evidence.
- Numerical details absent from an abstract are recorded as NR.
- No individual diagnosis, causation, or treatment directive is permitted.
- Full-text screening, formal risk-of-bias assessment, human adjudication, and
  clinical sign-off remain pending.

## Reproducibility

- Every copied input and generated prompt is SHA-256 hashed.
- Claude CLI version, requested model alias, canonical model returned by the
  provider, token usage, cost, terminal status, and any retry are retained.
- Screening is paired record-for-record with the same ordered 140-record sample
  in each of seven modules.
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_record(source: Path, target: Path, role: str) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if source.read_bytes() != target.read_bytes():
        raise RuntimeError(f"copy mismatch: {source} -> {target}")
    return {
        "role": role,
        "source_path": str(source),
        "packet_path": str(target),
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
    }


def assert_blind(packet_root: Path) -> None:
    forbidden = (
        "ai_r1",
        "claim_evidence_matrix",
        "psychiatrist_brief",
        "verified_references",
        "systematic_evidence_review_full",
    )
    violations = [
        str(path)
        for path in packet_root.rglob("*")
        if path.is_file() and any(token in path.name for token in forbidden)
    ]
    if violations:
        raise RuntimeError(f"Reviewer-1 artifacts leaked into blind packet: {violations}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-root", type=Path, default=DEFAULT_BLIND_ROOT)
    args = parser.parse_args()
    blind_root = args.blind_root.resolve()
    if blind_root.exists() and any(blind_root.iterdir()):
        raise FileExistsError(
            f"Refusing to overwrite non-empty blind packet: {blind_root}"
        )

    (blind_root / "inputs" / "modules").mkdir(parents=True, exist_ok=True)
    (blind_root / "outputs" / "modules").mkdir(parents=True, exist_ok=True)
    (blind_root / "prompts").mkdir(parents=True, exist_ok=True)
    (blind_root / "provenance").mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, object]] = []
    copied.append(
        copy_record(
            SOURCE_PROTOCOL,
            blind_root / "inputs" / "source_protocol.md",
            "unaltered source protocol",
        )
    )
    copied.append(
        copy_record(
            SOURCE_PROMPT,
            blind_root / "inputs" / "original_prompt.txt",
            "unaltered original review prompt",
        )
    )
    copied.append(
        copy_record(
            INPUTS / "claim_ledger.csv",
            blind_root / "inputs" / "claim_ledger.csv",
            "frozen 66-claim ledger",
        )
    )
    copied.append(
        copy_record(
            INPUTS / "README.md",
            blind_root / "inputs" / "review_input_readme.md",
            "review input scope",
        )
    )
    for shared_name in ("grounding_summary.json", "screening_scope_summary.json"):
        copied.append(
            copy_record(
                SOURCE_RUN / shared_name,
                blind_root / "inputs" / shared_name,
                "shared frozen rapid-review provenance",
            )
        )

    module_counts: dict[str, int] = {}
    for module, spec_name in MODULES.items():
        module_target = blind_root / "inputs" / "modules" / module
        copied.append(
            copy_record(
                INPUTS / spec_name,
                module_target / "spec.json",
                f"{module} protocol specification",
            )
        )
        for name in MODULE_FILES:
            copied.append(
                copy_record(
                    SOURCE_RUN / module / name,
                    module_target / name,
                    f"{module} frozen search or abstract record",
                )
            )
        candidates = json.loads(
            (module_target / "screening_candidates.json").read_text(encoding="utf-8")
        )
        if len(candidates) != 140:
            raise ValueError(f"{module}: expected 140 candidates, got {len(candidates)}")
        if any(not str(row.get("abstract") or "").strip() for row in candidates):
            raise ValueError(f"{module}: candidate without inspected abstract")
        module_counts[module] = len(candidates)

    contract_path = blind_root / "REVIEWER_CONTRACT.md"
    contract_path.write_text(CONTRACT, encoding="utf-8")
    copied.append(
        {
            "role": "secondary AI reviewer execution contract",
            "source_path": "generated by prepare_claude_secondary_review.py",
            "packet_path": str(contract_path),
            "bytes": contract_path.stat().st_size,
            "sha256": sha256(contract_path),
        }
    )

    assert sum(module_counts.values()) == 980
    assert_blind(blind_root)
    manifest = {
        "status": "frozen blind packet; Claude execution not yet started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_engine_root": str(ROOT),
        "source_engine_commit": "3f13858f4416664c89b8eb00d4d6a72947850584",
        "source_run": str(SOURCE_RUN),
        "blind_root": str(blind_root),
        "search_date": "2026-07-29",
        "profile": "rapid systematic evidence review; narrative synthesis",
        "advanced_to_each_ai_reviewer": sum(module_counts.values()),
        "module_candidate_counts": module_counts,
        "blindness_assertion": (
            "No filename associated with AI Reviewer-1 outputs or final Codex "
            "artifacts is present in this packet."
        ),
        "human_reviewer_2": "pending",
        "clinical_sign_off": "pending",
        "files": copied,
    }
    manifest_path = blind_root / "provenance" / "blind_input_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "blind_root": str(blind_root),
                "modules": len(module_counts),
                "screening_records": sum(module_counts.values()),
                "files": len(copied),
                "manifest_sha256": sha256(manifest_path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
