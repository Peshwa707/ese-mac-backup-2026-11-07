"""Offline sampling-frame builder."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.models import CandidateReview, SamplingFrame


def _candidate_items(raw) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("candidates"), list):
        return raw["candidates"]
    raise ValueError("candidate_reviews.json must be a list or contain 'candidates'")


def build_sampling_frame(input_path: Path, out_path: Path) -> SamplingFrame:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    candidates = [CandidateReview.model_validate(item) for item in _candidate_items(raw)]
    candidates = sorted(candidates, key=lambda c: c.candidate_id)
    frame = SamplingFrame(candidates=candidates)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frame.model_dump_json(indent=2), encoding="utf-8")
    return frame
