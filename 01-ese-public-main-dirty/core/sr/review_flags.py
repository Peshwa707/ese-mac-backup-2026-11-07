"""Human-review triggers — surface outcomes where automated output needs a human.

The benchmark validation exposed a *selective-extractability* bias: an outcome can be
eligible across many included studies yet pooled from only a few (the rest did not
report it, or it could not be cleanly extracted), which silently understates k. When
pooled k is far below the included-study count we flag the outcome for human review
rather than presenting the pooled estimate as if it were complete. This is the hook the
Phase-4 ML disagreement queue also feeds into.
"""

from __future__ import annotations


def selective_extraction_flags(stats, n_included: int, *, threshold: float = 0.5) -> list[str]:
    """One warning per pooled outcome whose k is < ``threshold`` of the included studies."""
    if not stats or not getattr(stats, "available", False) or n_included <= 0:
        return []
    outcomes = list(getattr(stats, "outcomes", []) or []) + \
        list(getattr(stats, "hr_outcomes", []) or [])
    flags: list[str] = []
    for oc in outcomes:
        k = getattr(oc, "n_pooled", 0) or 0
        if 0 < k < threshold * n_included:
            flags.append(
                f"HUMAN REVIEW — '{getattr(oc, 'label', oc)}' pooled from only {k}/{n_included} "
                f"included studies (<{int(threshold * 100)}%): possible selective-extractability "
                "bias. Confirm the remaining studies genuinely did not report this outcome before "
                "trusting the pooled estimate."
            )
    return flags
