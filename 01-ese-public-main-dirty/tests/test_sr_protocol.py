"""Protocol-writer: spec validation, deterministic query/compile, vertical validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from core.sr.config import SRConfig
from core.sr.protocol.compile import compile_vertical, render_protocol_md
from core.sr.protocol.pubmed_query import build_query
from core.sr.protocol.spec import (
    AnalysisPlan, ConceptBlock, ExtractionField, ExtractionGroup, Outcome,
    ProtocolSpec, SearchSpec,
)
from core.sr.protocol.validate import validate_vertical


def _obs_spec(pool_field="effect_total_fat", designs=("cohort", "case-control")):
    return ProtocolSpec(
        title="Dietary total fat and pancreatic cancer risk",
        short_id="diet-pc-test",
        framing="pico", profile="systematic",
        background="Test background.",
        population="Adults >=18y", intervention="Dietary total fat intake",
        comparator="Lowest intake category",
        outcomes_primary=[Outcome(name="Pancreatic cancer (highest vs lowest)", pool_field=pool_field)],
        designs=list(designs),
        inclusion=["Observational design", "Reports RR/OR/HR with 95% CI"],
        exclusion=["Animal study", "Review"],
        search=SearchSpec(concept_blocks=[
            ConceptBlock(name="population", mesh=["Pancreatic Neoplasms"], terms=["pancreatic cancer"]),
            ConceptBlock(name="exposure", mesh=["Dietary Fats"], terms=["total fat", "fat intake"]),
            ConceptBlock(name="design", terms=["cohort", "case-control"]),
        ], date_from="1990/01/01", date_to="2024/05/31"),
        rob_tool="auto",
        analysis=AnalysisPlan(dose_response=True, subgroups=["sex", "energy adjustment"]),
        extraction_groups=[
            ExtractionGroup(group="population", fields=[ExtractionField(name="total_n"),
                                                        ExtractionField(name="design")]),
            ExtractionGroup(group="efficacy", fields=[ExtractionField(name=pool_field)]),
        ],
    )


def test_spec_rejects_unknown_pool_field():
    with pytest.raises(ValidationError):
        ProtocolSpec(
            title="x", short_id="x", population="p", intervention="i",
            inclusion=["a"], exclusion=["b"],
            outcomes_primary=[Outcome(name="o", pool_field="ghost")],
            search=SearchSpec(concept_blocks=[ConceptBlock(name="p", terms=["t"])]),
            extraction_groups=[ExtractionGroup(group="g", fields=[ExtractionField(name="real")])],
        )


def test_spec_rejects_empty_eligibility():
    with pytest.raises(ValidationError):
        ProtocolSpec(
            title="x", short_id="x", population="p", intervention="i",
            inclusion=[], exclusion=["b"],
            search=SearchSpec(concept_blocks=[ConceptBlock(name="p", terms=["t"])]),
            extraction_groups=[ExtractionGroup(group="g", fields=[ExtractionField(name="r")])],
        )


def test_build_query_shape():
    q = build_query([ConceptBlock(name="p", mesh=["Pancreatic Neoplasms"], terms=["pancreatic cancer"]),
                     ConceptBlock(name="e", terms=["total fat"])],
                    "1990/01/01", "2024/05/31")
    assert '"Pancreatic Neoplasms"[Mesh]' in q and "pancreatic cancer[tiab]" in q
    assert " AND " in q and '("1990/01/01"[dp] : "2024/05/31"[dp])' in q
    assert "total fat[tiab]" in q


def test_compile_observational_writes_nos_and_loads(tmp_path: Path):
    spec = _obs_spec()
    written = compile_vertical(spec, tmp_path)
    names = {p.name for p in written}
    assert {"pico.yaml", "search_strategies.yaml", "extraction_schema.yaml",
            "rob_config.yaml", "grade_config.yaml"} <= names  # observational -> NOS emitted

    cfg = SRConfig(tmp_path)
    assert cfg.review_title.startswith("Dietary total fat")
    assert cfg.short_id == "diet-pc-test"
    assert "pancreatic cancer[tiab]" in cfg.pubmed_query
    assert cfg.inclusion_criteria and cfg.exclusion_criteria
    fields = {f["name"] for f in cfg.extraction_fields()}
    assert {"total_n", "design", "effect_total_fat"} <= fields
    schema = yaml.safe_load((tmp_path / "extraction_schema.yaml").read_text())
    efficacy = schema["fields"]["efficacy"][0]
    assert efficacy["pool"] == "log_effect"
    assert efficacy["label"] == "Pancreatic cancer (highest vs lowest)"


def test_compile_rct_omits_nos(tmp_path: Path):
    spec = _obs_spec(designs=("RCT",))
    spec = spec.model_copy(update={"rob_tool": "rob2"})
    written = {p.name for p in compile_vertical(spec, tmp_path)}
    assert "rob_config.yaml" not in written      # RCT uses shared defaults
    assert "grade_config.yaml" not in written


def test_validate_good_vertical_passes(tmp_path: Path):
    compile_vertical(_obs_spec(), tmp_path)
    rep = validate_vertical("diet-pc-test", root=tmp_path)
    assert rep.ok and not rep.errors
    # effect_total_fat declares pool: log_effect -> no poolable warning for it
    assert not any("effect_total_fat" in w for w in rep.warnings)


def test_validate_flags_unregistered_poolable_field(tmp_path: Path):
    # A field that looks poolable but lacks a pool marker is flagged.
    compile_vertical(_obs_spec(), tmp_path)
    schema_path = tmp_path / "extraction_schema.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    schema["fields"]["efficacy"].append({"name": "effect_sodium", "level": "abstract"})
    schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    rep = validate_vertical("x", root=tmp_path)
    assert rep.ok  # structurally valid...
    assert any("effect_sodium" in w and "pool marker" in w.lower() for w in rep.warnings)


def test_render_protocol_md_sections():
    md = render_protocol_md(_obs_spec())
    for h in ["# Protocol", "Review question", "Eligibility", "search strategy",
              "Data extraction", "Risk of bias", "statistical analysis"]:
        assert h in md
    assert "Newcastle-Ottawa" in md           # observational RoB
    assert "Test background." in md            # background carried through
    assert "pancreatic cancer[tiab]" in md     # generated query embedded in the doc
