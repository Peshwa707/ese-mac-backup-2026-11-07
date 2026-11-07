"""Phase 0.5 check #3 — the screening-prompt FREEZE is enforced here.

These golden tests are the freeze mechanism: if anyone edits the title/abstract
screening prompt (system text or user scaffolding in ``core.sr.screen``), the
fingerprint changes and these tests FAIL. A failure is not a bug to silence — it
means a Phase-1a recall bound built on the old prompt no longer transfers. The
correct response is: re-review the prompt change, re-run the freeze, update the
golden constants, and re-calibrate (treat it like a model swap).
"""

from __future__ import annotations

import hashlib

from core.sr.screen import _SYSTEM
from evals.screening_recall.prompt_freeze import compute_fingerprint, render_canonical

# Frozen 2026-06-19 against ESE b4cb2ba (Opus 4.8-era prompt). To change these,
# you must justify the prompt edit AND re-calibrate — see module docstring.
GOLDEN_PROMPT_SHA = "67d780247d12d3a15cb4ab666cbb4886dfb7850637c6d3bdb548abeb9c83f035"
GOLDEN_SYSTEM_SHA = "6905b1957a5a9171f35edbe4dae7ad629442e66a6c088bc4a2eb47171dadec24"


def test_screening_prompt_fingerprint_is_frozen():
    fp = compute_fingerprint()
    assert fp["sha256"] == GOLDEN_PROMPT_SHA, (
        "Screening prompt template changed — a recall bound calibrated on the old "
        "prompt no longer transfers. Re-review, re-freeze, re-calibrate."
    )


def test_system_prompt_fingerprint_is_frozen():
    # Pins the _SYSTEM text specifically — the injection fence lives here, so this
    # catches a fence weakening even if the user-scaffolding hash were also updated.
    fp = compute_fingerprint()
    assert fp["system_sha256"] == GOLDEN_SYSTEM_SHA


def test_fingerprint_is_deterministic():
    # No timestamp/commit may leak into the hashed material.
    assert compute_fingerprint()["sha256"] == compute_fingerprint()["sha256"]


def test_all_three_extra_branches_are_frozen():
    # Forge C2: the "the intervention class" sub-branch (modality declared, no arms)
    # is reachable wording the LLM can see — it must be in the frozen material.
    render = render_canonical()
    assert "the study design" in render                       # default branch
    assert "the treatment arm (arm-x / arm-y)" in render      # modality + arms
    assert "the treatment arm (the intervention class)" in render  # modality, no arms


def test_record_json_key_order_is_pinned():
    # Forge C1: the per-record JSON key order is part of the frozen wire format.
    # A reorder or sort_keys=True would move the hash; pin a byte-exact line so the
    # failure reason is legible ("key order changed") not a mystery re-calibrate.
    render = render_canonical()
    expected_line = (
        '{"pmid": "PMID0001", "title": "A canonical trial title", '
        '"study_type": "RCT", "abstract": "A fixed abstract sentence."}'
    )
    assert expected_line in render, "screening-prompt record JSON key order/shape changed"


def test_embedded_render_matches_its_own_hash():
    # Forge S3: guard against a hand-edited fingerprint JSON where canonical_render
    # and sha256 silently disagree.
    fp = compute_fingerprint()
    assert hashlib.sha256(fp["canonical_render"].encode("utf-8")).hexdigest() == fp["sha256"]


def test_injection_fence_semantics_present():
    # Semantic guard, not just byte-identity: the three fence properties must hold
    # in the live system prompt. A "freeze update" that quietly drops the fence
    # would still trip this.
    sys_lower = _SYSTEM.lower()
    assert "<records>" in sys_lower and "</records>" in sys_lower, "data delimiter missing"
    assert "data" in sys_lower and "never" in sys_lower and "instruction" in sys_lower, (
        "treat-as-data-not-instructions fence missing"
    )
    assert "only the text provided" in sys_lower, "no-outside-knowledge guard missing"
