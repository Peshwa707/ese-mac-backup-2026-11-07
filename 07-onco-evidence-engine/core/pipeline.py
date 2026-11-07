"""End-to-end orchestration: case text -> grounded Report (+ provenance).

Stages (spec §3): de-id -> structure -> query expansion -> parallel retrieval ->
ID verification -> grading -> conflict -> synthesis -> adversarial check ->
output. PHI surviving de-id raises before any external call.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from core.config.vertical import VerticalConfig, load_vertical
from core.connectors import connectors_for_vertical
from core.connectors.base import Connector
from core.grounding import deid, query_expansion, retrieval, structure, verify
from core.grounding.deid import PHIDetectedError  # re-exported for callers
from core.models import (
    DeidReport,
    Provenance,
    Report,
    SourceStatus,
)
from core.render import provenance as provenance_store
from core.synthesis import claim_checker, conflict, grading, synthesize

__all__ = ["run_case", "PHIDetectedError"]

# Generic fallback disclaimer used only when the active vertical supplies none.
# Kept domain-neutral so core/ holds no domain voice (the oncology wording lives
# in the vertical config and overrides this via ``vertical.disclaimer``).
_DISCLAIMER = (
    "Decision SUPPORT only — not autonomous or end-user-facing advice. All "
    "claims are citation-grounded; verify against primary sources before use."
)


async def run_case(
    raw_text: str,
    *,
    vertical_name: Optional[str] = None,
    make_manuscript: bool = False,
    connectors: Optional[list[Connector]] = None,
    report_id: Optional[str] = None,
) -> Report:
    from core.llm import client as _llm
    _llm.reset_stage_log()                       # fresh per-stage model provenance per run
    vertical: VerticalConfig = load_vertical(vertical_name)
    report_id = report_id or uuid.uuid4().hex
    prov = Provenance(report_id=report_id)

    # ① DE-ID — hard stop before any external call if PHI survives.
    # The vertical supplies its clinical caps allowlist so the generic ALL-CAPS
    # name detector does not redact domain acronyms (core stays domain-neutral).
    redacted, redactions = deid.deidentify(raw_text, vertical.deid_caps_allowlist)
    deid_report = DeidReport(redacted_text=redacted, redactions=redactions, clean=True)

    report = Report(
        id=report_id,
        vertical=vertical.name,
        provenance_id=report_id,
        disclaimer=vertical.disclaimer or _DISCLAIMER,
    )

    # ② STRUCTURE
    structured = structure.structure_case(redacted, vertical)
    report.structured_case = structured

    # ③ QUERY EXPANSION
    queries = query_expansion.build_queries(structured, vertical.search_strategies)
    prov.queries = [q for qs in queries.values() for q in qs]

    own_connectors = connectors is None
    # When no connectors are injected, build them from the active vertical's
    # configured source set (boundary C4) rather than a hardcoded default.
    conns = connectors if connectors is not None else connectors_for_vertical(vertical)
    try:
        # ④ PARALLEL RETRIEVAL
        results, merged = await retrieval.retrieve(conns, queries, structured)
        prov.raw_results = results
        report.sources_status = [
            SourceStatus(source=r.source, available=r.available, note=r.note)
            for r in results
        ] or [SourceStatus(source=c.source, available=False, note="not queried") for c in conns]

        # ⑤ ID VERIFICATION (real round-trip)
        verified, rejected_ids = await verify.verify_citations(conns, merged)
        prov.verification_trail = [
            {"key": c.key, "source": c.source, "verified": True} for c in verified
        ]
        prov.rejected.extend(rejected_ids)

        # ⑥/⑦ EVIDENCE GRADING + SYNTHESIS (retrieval-only)
        if not verified:
            report.empty_reason = "no verified evidence found"
            prov.stage_models = _llm.stage_log()
            provenance_store.write(prov)
            return report

        findings = synthesize.synthesize_findings(structured, verified, vertical)
        findings = grading.grade_findings(findings, vertical.search_strategies)

        # cite-or-omit + quote-anchoring against the verified pool
        verified_keys = {c.key for c in verified}
        findings, dropped_cite = verify.apply_cite_or_omit(findings, verified_keys)
        prov.rejected.extend(dropped_cite)

        # ⑦ CONFLICT surfacing
        findings = conflict.detect_conflicts(findings, vertical)
        # Snapshot the post-conflict list: any finding dropped downstream (claim
        # check) that had contested a survivor must leave an audit trail.
        post_conflict = list(findings)

        # ⑧ ADVERSARIAL CLAIM-CHECK
        findings, dropped_claims = claim_checker.filter_supported(findings, vertical)
        prov.rejected.extend(dropped_claims)

        # AH F8 — record conflicts whose counterpart was dropped after detection,
        # so a now-removed finding's contestation of a survivor isn't lost.
        kept_statements = {f.statement for f in findings}
        prov.rejected.extend(
            conflict.lost_conflict_records(post_conflict, kept_statements)
        )

        report.decision_support = findings
        if not findings:
            report.empty_reason = "no claim survived verification"

        # ⑨ OUTPUT — optional CARE manuscript. Route any ungrounded-claim audit
        # flags into the provenance trail so they are auditable, not just carried
        # silently on the report.
        if make_manuscript and findings:
            manuscript, audit_flags = synthesize.generate_manuscript(
                structured, findings, vertical
            )
            report.manuscript = manuscript
            if audit_flags:
                prov.rejected.append(
                    {
                        "reason": "manuscript_claim_ungrounded",
                        "detail": "\n".join(audit_flags),
                    }
                )

        prov.stage_models = _llm.stage_log()
        provenance_store.write(prov)
        return report
    finally:
        if own_connectors:
            for c in conns:
                await c.aclose()
