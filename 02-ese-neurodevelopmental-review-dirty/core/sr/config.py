"""Loader for the systematic-review vertical config.

Reads pico.yaml / search_strategies.yaml / extraction_schema.yaml from
``verticals/systematic-review/``. Kept separate from core.config.vertical because
the SR vertical has its own shape (no case schema / structure prompts).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERTICALS = _REPO_ROOT / "verticals"
_DEFAULT_VERTICAL = "systematic-review"
_SR_ROOT = _VERTICALS / _DEFAULT_VERTICAL   # shared defaults (rob/grade/prompts)


def resolve_vertical(name: str | None = None) -> str:
    return name or os.environ.get("ESE_VERTICAL") or os.environ.get("SR_VERTICAL", _DEFAULT_VERTICAL)


def persisted_vertical(run_dir) -> str | None:
    """The vertical name persisted in a run's ``run_state.json`` (or None).

    Lets any run-aware entry point (bundle load, manuscript export) honor the
    vertical the run was created with instead of silently defaulting — the single
    source of truth for "which review is this run". Reads the JSON directly to
    avoid coupling config.py to the orchestrator state module.
    """
    import json
    try:
        p = Path(run_dir) / "run_state.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return ((data.get("state") or {}).get("vertical") or "") or None
    except Exception:
        return None


def vertical_root(name: str | None = None) -> Path:
    return _VERTICALS / resolve_vertical(name)


def shared_or_vertical(filename: str, name: str | None = None) -> Path:
    """A vertical's own file if present, else the systematic-review shared default.

    Domain-specific files (pico/search/extraction) live in the vertical; domain-
    agnostic ones (rob_config/grade_config/prompts) fall back to the shared SR
    vertical, so a new domain only needs to supply its PICO + search + dictionary.
    """
    own = vertical_root(name) / filename
    return own if own.exists() else (_SR_ROOT / filename)


_VALID_CONT_MEASURES = {"MD", "SMD", "ROM"}


def _valid_measure(measure: str, field: str) -> str:
    """Fail loud on an unknown continuous effect measure. Without this a typo makes
    meta::metacont() error, fit returns NULL, and the outcome silently disappears
    from the analysis with no diagnostic."""
    m = str(measure).strip().upper()
    if m not in _VALID_CONT_MEASURES:
        raise ValueError(
            f"outcome {field!r} declares measure {measure!r}; must be one of "
            f"{sorted(_VALID_CONT_MEASURES)} (fix the vertical extraction_schema.yaml)")
    return m


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"SR vertical config missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class SRConfig:
    def __init__(self, root: Path = _SR_ROOT):
        self.root = root
        self.vertical = root.name
        self.pico = _load(root / "pico.yaml")
        self.search = _load(root / "search_strategies.yaml")
        self.extraction = _load(root / "extraction_schema.yaml")

    # --- review identity ---
    @property
    def review_title(self) -> str:
        return " ".join((self.pico.get("review", {}).get("title") or "").split())

    @property
    def short_id(self) -> str:
        return self.pico.get("review", {}).get("short_id", "sr-run")

    @property
    def output_base(self) -> str:
        """Per-vertical manuscript output directory (optional; '' = engine default)."""
        return self.pico.get("review", {}).get("output_base", "")

    @property
    def disclaimer(self) -> str:
        return " ".join((self.pico.get("disclaimer") or "").split())

    # --- search ---
    @property
    def pubmed_query(self) -> str:
        pm = self.search.get("pubmed", {})
        base = " ".join((pm.get("query") or "").split())
        # Spelling/synonym-robust recall: OR in declared variant queries so records
        # that misspell a drug (e.g. 'remazolam' for 'remimazolam') are not missed.
        variants = [" ".join(str(v).split()) for v in (pm.get("variant_queries") or []) if v]
        if variants:
            return " OR ".join(f"({p})" for p in [base, *variants] if p)
        return base

    @property
    def retmax(self) -> int:
        return int(self.search.get("pubmed", {}).get("retmax", 100))

    def source_query(self, name: str) -> str:
        """Per-source query string (falls back to the PubMed query)."""
        if name == "pubmed":
            return self.pubmed_query
        block = self.search.get(name)
        if isinstance(block, dict) and block.get("query"):
            return " ".join(block["query"].split())
        for d in (self.search.get("other_databases") or []):
            if str(d.get("name", "")).lower() == name.lower() and d.get("query"):
                return " ".join(d["query"].split())
        return self.pubmed_query

    @property
    def named_trials(self) -> list[str]:
        return list(self.search.get("named_trials") or [])

    @property
    def sentinel_strict(self) -> bool:
        """If true, a named-trial sentinel violation raises (fails the run) instead
        of only being recorded as a warning. Default false."""
        return bool(self.pico.get("review", {}).get("sentinel_strict", False))

    @property
    def strict_stream_validation(self) -> bool:
        """Fail screening when included records lack the declared stream label."""
        return bool(self.pico.get("review", {}).get("strict_stream_validation", False))

    @property
    def reference_mining_seeds(self) -> list[dict[str, str]]:
        return list(self.search.get("reference_mining_seeds") or [])

    @property
    def not_executed_databases(self) -> list[dict[str, Any]]:
        return [d for d in (self.search.get("other_databases") or []) if not d.get("executed")]

    # --- eligibility (rendered into the screening prompt) ---
    @property
    def inclusion_criteria(self) -> list[str]:
        return list(self.pico.get("eligibility", {}).get("inclusion") or [])

    @property
    def exclusion_criteria(self) -> list[str]:
        return list(self.pico.get("eligibility", {}).get("exclusion") or [])

    @property
    def exclusion_buckets(self) -> list[dict[str, Any]]:
        """Domain-specific PRISMA exclusion-reason buckets ({label, keywords})."""
        return list(self.pico.get("eligibility", {}).get("exclusion_buckets") or [])

    def pico_text(self) -> str:
        p = self.pico.get("pico", {})
        out = []
        for k in ("population", "intervention", "comparator"):
            v = " ".join((p.get(k) or "").split())
            if v:
                out.append(f"{k.capitalize()}: {v}")
        return "\n".join(out)

    def pcc_text(self) -> str:
        """Population–Concept–Context framing for scoping reviews."""
        p = self.pico.get("pcc", {})
        out = []
        for k in ("population", "concept", "context"):
            v = " ".join((p.get(k) or "").split())
            if v:
                out.append(f"{k.capitalize()}: {v}")
        return "\n".join(out)

    def framing_text(self, framing: str) -> str:
        return self.pcc_text() if framing == "pcc" else self.pico_text()

    # --- extraction field list (drives the extraction prompt + table) ---
    def extraction_fields(self) -> list[dict[str, str]]:
        """Flat list of {group, name, level} the extractor must fill."""
        out: list[dict[str, str]] = []
        fields = self.extraction.get("fields") or {}
        for group, items in fields.items():
            for it in items:
                out.append({"group": group, "name": it["name"], "level": it.get("level", "abstract")})
        return out

    @property
    def extraction_rules(self) -> list[str]:
        return list(self.extraction.get("rules") or [])

    # --- synthesis (meta-analysis pooling + subgroup axis), all vertical-driven ---
    def poolable_fields(self) -> dict[str, list[tuple[str, str]]]:
        """Outcome fields the meta-analysis may pool, declared in the extraction
        schema via ``pool: proportion|log_effect`` (+ optional ``label``).

        Returns ``{"proportion": [(field, label), ...], "log_effect": [...]}``.
        If a vertical declares no ``pool`` markers, no outcomes are pooled; the
        extraction schema is the source of truth.
        """
        prop: list[tuple[str, str]] = []
        log: list[tuple[str, str]] = []
        for items in (self.extraction.get("fields") or {}).values():
            for it in items:
                kind = (it.get("pool") or "").lower()
                label = it.get("label", it["name"])
                if kind == "proportion":
                    prop.append((it["name"], label))
                elif kind in ("log_effect", "hr"):
                    log.append((it["name"], label))
        return {"proportion": prop, "log_effect": log}

    def _paired_pool_fields(
        self,
        pool_kind: str,
        required: tuple[str, ...],
        optional: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for items in (self.extraction.get("fields") or {}).values():
            for it in items:
                if (it.get("pool") or "").lower() != pool_kind:
                    continue
                pair = it.get("pair") or {}
                if not isinstance(pair, dict) or any(not pair.get(k) for k in required):
                    continue
                out.append({
                    "field": it["name"],
                    "label": it.get("label", it["name"]),
                    "pair": {k: str(pair[k]) for k in required}
                            | {k: str(pair[k]) for k in optional if pair.get(k)},
                    **({"beneficial": it.get("beneficial")}
                       if "beneficial" in it else {}),
                    # Optional per-outcome effect measure (e.g. MD for same-unit
                    # continuous outcomes). Falls back to the caller default (SMD).
                    # Validate up front: an unknown measure would make metacont() error
                    # and the outcome silently vanish from the analysis (empty CSV).
                    **({"measure": _valid_measure(it["measure"], it["name"])}
                       if it.get("measure") else {}),
                })
        return out

    def binary_2x2_fields(self) -> list[dict[str, Any]]:
        """Binary two-arm outcomes declared with ``pool: binary_2x2``.

        ``beneficial`` is intentionally nullable. If omitted, number-needed
        output remains neutral because event desirability cannot be inferred
        from the 2x2 table itself.
        """
        return self._paired_pool_fields(
            "binary_2x2", ("events_tx", "n_tx", "events_ctrl", "n_ctrl"))

    def continuous_fields(self) -> list[dict[str, Any]]:
        """Continuous two-arm outcomes declared with ``pool: continuous``."""
        return self._paired_pool_fields(
            "continuous",
            ("n_tx", "mean_tx", "sd_tx", "n_ctrl", "mean_ctrl", "sd_ctrl"),
            ("median_tx", "q1_tx", "q3_tx", "lo_tx", "hi_tx",
             "median_ctrl", "q1_ctrl", "q3_ctrl", "lo_ctrl", "hi_ctrl"),
        )

    @property
    def synthesis(self) -> dict[str, Any]:
        return self.pico.get("synthesis", {}) or {}

    @property
    def subgroup_field(self) -> str:
        """Extracted field that defines the subgroup/stratum axis (e.g. modality,
        study design). Empty string = no subgrouping."""
        return self.synthesis.get("subgroup_field", "")

    @property
    def subgroup_map(self) -> dict[str, list[str]]:
        """Canonical-label -> match-substring(s). Empty = identity (no remap)."""
        return {k: [str(x).lower() for x in (v or [])]
                for k, v in (self.synthesis.get("subgroup_map") or {}).items()}

    @property
    def anchor_terms(self) -> list[str]:
        """Substrings identifying the shared reference (anchor) comparator for an
        anchored indirect comparison. When set, HR pooling drops studies whose
        comparator_label does not match the anchor. Empty = no comparator filter
        (backward compatible). Domain-specific terms live in the vertical config."""
        return [str(t).lower() for t in (self.synthesis.get("anchor_terms") or [])]

    @property
    def synthesis_arms(self) -> list[str]:
        """Display arms for the evidence brief. Empty = none."""
        return list(self.synthesis.get("arms") or [])

    @property
    def indirect_comparison(self) -> bool:
        """True when no head-to-head RCT exists between arms (indirect only)."""
        return bool(self.synthesis.get("indirect_comparison", False))

    @property
    def reviewers(self) -> dict:
        """Per-reviewer model roster: name -> {source, model, endpoint}. Empty = the
        orchestrator's defaults (R1 anthropic / R2 local). See orchestrator.reviewers."""
        return self.pico.get("reviewers", {}) or {}

    @property
    def dose_outcome(self) -> str:
        """Outcome field the dose-response analysis targets ('' = none)."""
        return self.synthesis.get("dose_outcome", "")

    @property
    def templates_dir(self) -> Path:
        # Templates (PRISMA flow, tables) are domain-agnostic — fall back to shared.
        own = self.root / "templates"
        return own if own.is_dir() else (_SR_ROOT / "templates")


@lru_cache(maxsize=8)
def _load_cached(name: str) -> SRConfig:
    return SRConfig(_VERTICALS / name)


def load_sr_config(name: str | None = None) -> SRConfig:
    """Load the active SR vertical (SR_VERTICAL env or explicit name)."""
    return _load_cached(resolve_vertical(name))
