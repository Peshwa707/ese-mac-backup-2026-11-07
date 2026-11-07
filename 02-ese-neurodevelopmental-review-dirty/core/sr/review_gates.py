"""Record-level governance gates for dual-AI review followed by human review.

The AI reviewers are independent evidence checks, not substitutes for the named
human Reviewer 2 or clinical sign-off.  This module is deliberately small and
pure so it can be called by validators, CI, or a future export gate without
performing network or filesystem work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class GateResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _status(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    return str(block.get("status") or block.get("decision") or "").strip().lower()


def _complete(block: Any) -> bool:
    return _status(block) in {"complete", "completed", "approved", "reviewed", "done"}


def validate_review_gates(record: dict[str, Any]) -> GateResult:
    """Validate the dual-AI and human-expert sequence for one evidence record.

    Required sequence:
      1. independent Codex/Reviewer-1 AI output;
      2. independent Claude/secondary-AI output;
      3. named human Reviewer 2 decision and reconciliation;
      4. qualified clinical sign-off before a clinical package unlocks.
    """
    errors: list[str] = []
    ai1 = record.get("ai_reviewer_1")
    ai2 = record.get("ai_reviewer_2")
    human = record.get("human_reviewer_2")
    reconciliation = record.get("reconciliation")
    clinical = record.get("clinical_signoff")

    for key, block in (("ai_reviewer_1", ai1), ("ai_reviewer_2", ai2)):
        if not isinstance(block, dict):
            errors.append(f"{key} is missing")
            continue
        if not _complete(block):
            errors.append(f"{key} is not complete")
        if not str(block.get("provider") or block.get("model_or_agent") or "").strip():
            errors.append(f"{key} has no provider/model provenance")
        if not str(block.get("timestamp") or "").strip():
            errors.append(f"{key} has no timestamp")

    if isinstance(ai1, dict) and isinstance(ai2, dict):
        p1 = str(ai1.get("provider") or ai1.get("model_or_agent") or "").strip().lower()
        p2 = str(ai2.get("provider") or ai2.get("model_or_agent") or "").strip().lower()
        if p1 and p2 and p1 == p2:
            errors.append("ai_reviewer_1 and ai_reviewer_2 must have independent providers")

    if not isinstance(human, dict):
        errors.append("human_reviewer_2 is missing")
    else:
        if not _complete(human):
            errors.append("human_reviewer_2 is not complete")
        name = str(human.get("reviewer_name") or human.get("reviewer") or "").strip()
        if not name:
            errors.append("human_reviewer_2 has no named expert")
        if re.search(r"\b(codex|claude|fable|opus|ai reviewer|artificial intelligence)\b",
                     name.lower()):
            errors.append("human_reviewer_2 must not contain an AI provider identity")

    if not isinstance(reconciliation, dict) or not _complete(reconciliation):
        errors.append("reconciliation is missing or incomplete")

    if not isinstance(clinical, dict):
        errors.append("clinical_signoff is missing")
    elif str(clinical.get("status") or "").strip().lower() not in {"approved", "not_applicable"}:
        errors.append("clinical_signoff is not approved")

    return GateResult(ok=not errors, errors=errors)
