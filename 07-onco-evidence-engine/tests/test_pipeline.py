"""Live end-to-end integration. Skipped without network + ANTHROPIC_API_KEY.

Asserts the anti-hallucination invariants on a real run: every citation is
verified, and every finding traces to a verified source.
"""

from __future__ import annotations

import os

import pytest

from core.pipeline import run_case

pytestmark = pytest.mark.live

_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(not _HAS_KEY, reason="requires ANTHROPIC_API_KEY + network")
async def test_golden_case_is_fully_grounded(deid_case_clean):
    report = await run_case(deid_case_clean, make_manuscript=False)

    # Every surfaced citation must be verified (no invented ids survive).
    for finding in report.decision_support:
        assert finding.citations, "finding has no citation (cite-or-omit failed)"
        assert all(c.verified for c in finding.citations)

    # Either we produced grounded findings or an explicit empty state — never
    # fabricated content.
    if not report.decision_support:
        assert report.empty_reason
