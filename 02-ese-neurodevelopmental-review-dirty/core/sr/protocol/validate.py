"""Validate a compiled vertical: it must load via SRConfig and declare poolable
outcomes in the extraction schema. Closes the engine's missing-validator gap.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.sr.config import SRConfig, shared_or_vertical, vertical_root


@dataclass
class ValidationReport:
    name: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pubmed_count: int | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "errors": self.errors,
                "warnings": self.warnings, "pubmed_count": self.pubmed_count}


def _poolable(cfg: SRConfig) -> set[str]:
    declared = set()
    pf = cfg.poolable_fields()
    declared.update(field for field, _ in pf["proportion"])
    declared.update(field for field, _ in pf["log_effect"])
    declared.update(spec["field"] for spec in cfg.binary_2x2_fields())
    declared.update(spec["field"] for spec in cfg.continuous_fields())
    return declared


def _narrative_only(cfg: SRConfig) -> set[str]:
    """Fields explicitly reserved for narrative synthesis.

    A review can declare an outcome for GRADE/claim tracking while deliberately
    refusing automatic pooling because estimates are not commensurable by
    default. The explicit marker prevents the validator's heuristic warning from
    treating that intentional choice as a missing pool declaration.
    """
    return {
        item["name"]
        for items in (cfg.extraction.get("fields") or {}).values()
        for item in items
        if str(item.get("pool", "")).lower() in {"narrative", "none", "never"}
    }


def _esearch_count(query: str) -> int | None:
    try:
        url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
               "?db=pubmed&rettype=count&retmode=json&term=" + urllib.parse.quote(query))
        with urllib.request.urlopen(url, timeout=30) as r:
            import json
            return int(json.load(r)["esearchresult"]["count"])
    except Exception:
        return None


def _validate_review_contract(name: str, root: Path, errors: list[str]) -> None:
    """Validate optional record-level governance artifacts for a vertical."""
    contract = root.parent.parent / "review_inputs" / name / "evidence_record_schema.yaml"
    if not contract.exists():
        return
    try:
        schema = yaml.safe_load(contract.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        errors.append(f"evidence_record_schema load failed: {exc}")
        return
    required = set(schema.get("required") or [])
    for field in ("ai_reviewer_1", "ai_reviewer_2", "human_reviewer_2",
                  "reconciliation", "clinical_signoff"):
        if field not in required:
            errors.append(f"evidence_record_schema missing mandatory gate '{field}'")
    statuses = set((schema.get("properties", {}).get("claim_status", {}).get("values") or []))
    if statuses and "rejected" in statuses and "claim_status_rules" not in schema:
        errors.append("claim_status 'rejected' has no operational falsification rule")


def validate_vertical(name: str, *, root: Path | None = None,
                      check_count: bool = False) -> ValidationReport:
    """Structurally validate a vertical. `root` overrides verticals/<name>/ (for ad-hoc dirs).
    check_count hits PubMed for a recall sanity test."""
    errors: list[str] = []
    warnings: list[str] = []
    root = Path(root) if root is not None else vertical_root(name)
    if not root.is_dir():
        return ValidationReport(name, False, [f"vertical dir not found: {root}"])
    try:
        cfg = SRConfig(root)
    except Exception as exc:
        return ValidationReport(name, False, [f"SRConfig load failed: {exc}"])

    if not cfg.review_title:
        errors.append("review.title is empty")
    if not cfg.pubmed_query:
        errors.append("pubmed.query is empty")
    if not cfg.inclusion_criteria:
        errors.append("eligibility.inclusion is empty")
    if not cfg.exclusion_criteria:
        errors.append("eligibility.exclusion is empty")
    _validate_review_contract(name, root, errors)
    fields = {f["name"] for f in cfg.extraction_fields()}
    if not fields:
        errors.append("extraction_schema has no fields")

    pool = _poolable(cfg)
    narrative_only = _narrative_only(cfg)
    grade_path = (root / "grade_config.yaml") if (root / "grade_config.yaml").exists() \
        else shared_or_vertical("grade_config.yaml", name)
    try:
        grade = yaml.safe_load(grade_path.read_text()) or {}
    except Exception:
        grade = {}
    for oc in grade.get("outcomes", []):
        f = oc.get("field", "")
        if not f:
            continue
        if f not in fields:
            errors.append(f"GRADE outcome '{oc.get('name')}' field '{f}' is not an extraction field")
        elif f not in pool and f not in narrative_only:
            warnings.append(
                f"outcome field '{f}' is not declared with a supported pool marker "
                f"in extraction_schema.yaml — it will NOT pool until the field declares "
                f"pool: proportion, log_effect, binary_2x2, or continuous.")
    # Heuristic: an extraction field that looks poolable but isn't registered.
    for f in sorted(fields):
        if ((f.startswith("effect_") or f.endswith("_rate") or f.endswith("_hr"))
                and f not in pool and f not in narrative_only):
            warnings.append(f"extraction field '{f}' looks poolable but has no supported "
                            f"pool marker in extraction_schema.yaml.")

    count = None
    if check_count and cfg.pubmed_query:
        count = _esearch_count(cfg.pubmed_query)
        if count == 0:
            warnings.append("PubMed query returned 0 records — likely too narrow or malformed.")
        elif count is not None and count > 50000:
            warnings.append(f"PubMed query returned {count} records — likely too broad.")

    return ValidationReport(name, not errors, errors, warnings, count)
