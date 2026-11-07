"""Bounded risk-of-bias (RoB) domain judgments with rationale, source links,
and explicit abstention/unknown states.

Converts a set of per-domain risk-of-bias judgments into an auditable,
byte-stable assessment record. Every domain judgment is exactly one of five
bounded values (`low_risk`, `some_concerns`, `high_risk`, `unclear_risk`,
`not_assessed`); a judgment is never silently inferred or upgraded, and a
domain the source evidence does not support must be recorded as the explicit
`not_assessed` abstention state rather than invented.

Every domain judgment carries a mandatory, non-empty `rationale` and a bounded
list of free-text `source_links` describing where the judgment came from;
each `source_links` entry must already be a non-empty string -- malformed
values are rejected rather than silently coerced with `str()`. A
`not_assessed` judgment cannot carry `source_links` -- there is no source
evidence backing a judgment that was never made. Domain names must be in
canonical form (no leading/trailing whitespace) so that duplicate-domain
detection cannot be evaded by whitespace variants of the same name. `build_rob_assessment`
assembles domain judgments into a `rob_hash`-bound assessment, and refuses to
let an `overall_judgment` invent a determination when every domain judgment
is `not_assessed`. `verify_rob_assessment` re-validates a stored assessment
and detects any later tampering, mirroring the `object_hash`/`IntegrityError`
pattern used throughout `contracts.py`, `dedup.py`, `evaluation.py`,
`replay.py`, and `extraction.py`.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import IntegrityError, object_hash

ROB_ASSESSMENT_VERSION = "rob-assessment-v1"
JUDGMENTS = ("low_risk", "some_concerns", "high_risk", "unclear_risk", "not_assessed")


def build_domain_judgment(
    domain: str,
    judgment: str,
    rationale: str,
    source_links: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one bounded risk-of-bias domain judgment.

    `judgment` must be one of `JUDGMENTS`. `rationale` is always required,
    even for the `not_assessed` abstention state: it must say *why* the
    domain could not be assessed, never leaving the reason implicit.
    `source_links` are free-text references to the evidence the judgment was
    based on; a `not_assessed` judgment must carry none, since there is no
    evidence backing a judgment that was never made.
    """
    domain_name = domain.strip() if isinstance(domain, str) else ""
    if not domain_name:
        raise IntegrityError("domain judgment requires a non-empty 'domain' name")
    if judgment not in JUDGMENTS:
        raise IntegrityError(f"{domain_name}: unknown risk-of-bias judgment {judgment!r}")
    rationale_text = rationale.strip() if isinstance(rationale, str) else ""
    if not rationale_text:
        raise IntegrityError(f"{domain_name}: domain judgment requires a non-empty rationale")
    if any(not isinstance(link, str) for link in source_links):
        raise IntegrityError(f"{domain_name}: source_links entries must already be strings, not coerced")
    links = tuple(link.strip() for link in source_links)
    if any(not link for link in links):
        raise IntegrityError(f"{domain_name}: source_links entries must be non-empty")
    if judgment == "not_assessed" and links:
        raise IntegrityError(f"{domain_name}: not_assessed domain judgment must not carry source_links")
    return {
        "domain": domain_name,
        "judgment": judgment,
        "rationale": rationale_text,
        "source_links": list(links),
    }


def not_assessed_domain_judgment(domain: str, rationale: str) -> dict[str, Any]:
    """Build one explicit `not_assessed` (abstention/unknown) domain judgment."""
    return build_domain_judgment(domain, "not_assessed", rationale)


def _validate_domain_judgment(entry: Mapping[str, Any]) -> str:
    domain = entry.get("domain")
    if not isinstance(domain, str) or not domain.strip():
        raise IntegrityError("domain judgment entry is missing a non-empty 'domain' name")
    if domain != domain.strip():
        raise IntegrityError(
            f"{domain!r}: domain name must be in canonical form (no leading/trailing whitespace)"
        )
    judgment = entry.get("judgment")
    if judgment not in JUDGMENTS:
        raise IntegrityError(f"{domain}: unknown risk-of-bias judgment {judgment!r}")
    rationale = entry.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise IntegrityError(f"{domain}: domain judgment requires a non-empty rationale")
    links = entry.get("source_links")
    if not isinstance(links, list) or any(not isinstance(link, str) or not link.strip() for link in links):
        raise IntegrityError(f"{domain}: source_links must be a list of non-empty strings")
    if judgment == "not_assessed" and links:
        raise IntegrityError(f"{domain}: not_assessed domain judgment must not carry source_links")
    return domain


def _reject_invented_overall(overall_judgment: str, domains: Sequence[Mapping[str, Any]]) -> None:
    if overall_judgment != "not_assessed" and all(entry["judgment"] == "not_assessed" for entry in domains):
        raise IntegrityError(
            "overall_judgment must be 'not_assessed' when every domain judgment is 'not_assessed'"
        )


def build_rob_assessment(
    record_id: str,
    tool: str,
    domain_judgments: Sequence[Mapping[str, Any]],
    overall_judgment: str,
) -> dict[str, Any]:
    """Assemble a byte-stable, auditable risk-of-bias assessment record.

    Requires a non-empty `record_id` and `tool` (the name of the protocol-
    defined risk-of-bias tool used), at least one domain judgment with
    unique domain names, and a bounded `overall_judgment`. If every domain
    judgment is the `not_assessed` abstention state, `overall_judgment` must
    also be `not_assessed`: an overall risk-of-bias determination can never
    be invented when no domain evidence supports it. The returned record
    carries a `rob_hash` binding its exact content; `verify_rob_assessment`
    detects any later change.
    """
    if not isinstance(record_id, str) or not record_id.strip():
        raise IntegrityError("risk-of-bias assessment requires a non-empty record_id")
    if not isinstance(tool, str) or not tool.strip():
        raise IntegrityError("risk-of-bias assessment requires a non-empty tool")
    if not domain_judgments:
        raise IntegrityError("risk-of-bias assessment requires at least one domain judgment")
    if overall_judgment not in JUDGMENTS:
        raise IntegrityError(f"unknown overall risk-of-bias judgment {overall_judgment!r}")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for entry in domain_judgments:
        domain = _validate_domain_judgment(entry)
        if domain in seen:
            raise IntegrityError(f"duplicate risk-of-bias domain: {domain}")
        seen.add(domain)
        normalized.append(dict(entry))
    _reject_invented_overall(overall_judgment, normalized)
    body = {
        "version": ROB_ASSESSMENT_VERSION,
        "record_id": record_id.strip(),
        "tool": tool.strip(),
        "domains": normalized,
        "overall_judgment": overall_judgment,
    }
    return {**body, "rob_hash": object_hash(body)}


def verify_rob_assessment(assessment: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a stored risk-of-bias assessment's integrity and re-validate every domain.

    Raises `IntegrityError` if the assessment's `rob_hash` does not match its
    content (tamper detection), if the assessment version is unsupported, if
    `record_id` or `tool` is missing, empty, or not a string, if `domains`
    is missing, not a list, or empty, if any domain entry violates the
    domain-judgment invariants enforced by `build_domain_judgment`
    (including a `not_assessed` domain that carries `source_links`), if any
    domain name is duplicated, or if `overall_judgment` invents a
    determination while every domain judgment is `not_assessed` -- the same
    construction-time invariants enforced by `build_rob_assessment` are
    re-checked here, not just recomputed.
    """
    body = {key: value for key, value in assessment.items() if key != "rob_hash"}
    if assessment.get("rob_hash") != object_hash(body):
        raise IntegrityError("rob_hash does not match risk-of-bias assessment content")
    if body.get("version") != ROB_ASSESSMENT_VERSION:
        raise IntegrityError(f"unsupported risk-of-bias assessment version: {body.get('version')}")
    record_id = body.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise IntegrityError("risk-of-bias assessment requires a non-empty record_id")
    tool = body.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise IntegrityError("risk-of-bias assessment requires a non-empty tool")
    domains = body.get("domains")
    if not isinstance(domains, list) or not domains:
        raise IntegrityError("risk-of-bias assessment requires at least one domain judgment")
    overall_judgment = body.get("overall_judgment")
    if overall_judgment not in JUDGMENTS:
        raise IntegrityError(f"unknown overall risk-of-bias judgment {overall_judgment!r}")
    seen: set[str] = set()
    for entry in domains:
        if not isinstance(entry, Mapping):
            raise IntegrityError("risk-of-bias domain judgment entry must be a mapping")
        domain = _validate_domain_judgment(entry)
        if domain in seen:
            raise IntegrityError(f"duplicate risk-of-bias domain: {domain}")
        seen.add(domain)
    _reject_invented_overall(overall_judgment, domains)
    return body
