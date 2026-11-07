"""Pass-2 export-subsystem gate: render suppression + export_allowed enforcement.

When the orchestrator's honesty gates are not cleared for a run, the manuscript
export must be *suppressed* (no full manuscript, no per-component renders) rather
than silently shipping an un-vetted dossier. Legacy (non-orchestrated) run dirs
have no run_state.json and stay ungated for backward compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.sr.config import load_sr_config
from core.sr.manuscript.export import export_project
from core.sr.models import (
    ExtractedStudy,
    ExtractedValue,
    PrismaCounts,
    ScreenDecision,
    ScreeningResult,
    SRRun,
)


def _synthetic_run():
    return SRRun(
        run_id="deadbeefcafebabe",
        review_title="Test Review: A vs B",
        pubmed_query="(myeloma) AND (CAR-T OR bispecific)",
        counts=PrismaCounts(identified_pubmed=100, after_dedup=100, id_unverified_removed=0,
                            screened=100, excluded_screening=80, included=1, uncertain=19),
        exclusion_reasons={"review/editorial/no-outcome": 50},
        screening=[ScreeningResult(pmid="111", decision=ScreenDecision.INCLUDE,
                                   reason="phase 3 RCT", confidence=0.9, modality="CAR-T")],
        extractions=[ExtractedStudy(pmid="111", study_name="TRIALX", modality="CAR-T",
                     values=[ExtractedValue(field="orr", group="efficacy", value="84%", quote="ORR 84%")])],
        named_trial_capture={"CARTITUDE-4": True},
        limitations=["AI = Reviewer 1 only."],
        provenance_id="deadbeefcafebabe",
        disclaimer="First pass; Reviewer 2 required.",
    )


_RECORDS = {"111": {"pmid": "111", "title": "A trial of X", "year": 2024,
                    "abstract": "In this phase 3 trial the ORR was 84%."}}

_AUDIT = {"anchoring": {"anchored": 1, "substantive_values": 1, "anchored_rate": 1.0,
                        "estimated_for_human_review": 0, "nr": 1},
          "resolution": {"resolved": 1, "checked": 1}}


def test_export_blocked_suppresses_renders(tmp_path):
    """A failed gate must withhold the full manuscript + all component renders."""
    run, cfg = _synthetic_run(), load_sr_config()
    reasons = ["assemble: no signed human approval", "stats: status 'pending' (not done/approved)"]
    result = export_project(run, cfg, _RECORDS, audit=_AUDIT, base_dir=tmp_path, day="2026-06-16",
                            gate=(False, reasons))
    project = Path(result["project_dir"])
    # No dossier was rendered.
    assert result["export_blocked"] is True
    assert result["n_components"] == 0
    assert result["block_reasons"] == reasons
    assert not (project / "docx" / "99_full_manuscript.docx").exists()
    assert not (project / "markdown" / "99_full_manuscript.md").exists()
    assert not (project / "markdown").exists() or not list((project / "markdown").glob("*.md"))
    # The on-disk manifest must be machine-readably marked blocked (not "looks complete").
    manifest = json.loads((project / "manifest.json").read_text())
    assert manifest["export_blocked"] is True and manifest["block_reasons"] == reasons
    # A blocked notice records WHY, so the suppression is not silent.
    notice = project / "EXPORT_BLOCKED.md"
    assert notice.exists()
    body = notice.read_text(encoding="utf-8")
    assert "no signed human approval" in body and "not done/approved" in body


def test_export_blocked_purges_a_prior_rendered_dossier(tmp_path):
    """A blocked re-export onto a dir that already holds a full render must REMOVE the
    stale dossier — not leave a downloadable manuscript next to the block notice."""
    run, cfg = _synthetic_run(), load_sr_config()
    # First, a clean (allowed) export populates the deterministic project dir.
    ok = export_project(run, cfg, _RECORDS, audit=_AUDIT, base_dir=tmp_path, day="2026-06-16",
                        gate=(True, []))
    project = Path(ok["project_dir"])
    assert (project / "markdown" / "99_full_manuscript.md").exists()
    assert (project / "index.html").exists()
    # Now the gate goes red and we re-export to the SAME dir.
    blocked = export_project(run, cfg, _RECORDS, audit=_AUDIT, base_dir=tmp_path, day="2026-06-16",
                             gate=(False, ["assemble: no signed human approval"]))
    assert Path(blocked["project_dir"]) == project
    # The stale dossier is gone; only the block notice + blocked manifest remain.
    assert not (project / "markdown" / "99_full_manuscript.md").exists()
    assert not (project / "docx" / "99_full_manuscript.docx").exists()
    assert not (project / "index.html").exists()
    assert not list((project / "markdown").glob("*.md")) if (project / "markdown").exists() else True
    assert (project / "EXPORT_BLOCKED.md").exists()
    assert json.loads((project / "manifest.json").read_text())["export_blocked"] is True


def test_export_allowed_renders_full_manuscript(tmp_path):
    """A cleared gate renders normally."""
    run, cfg = _synthetic_run(), load_sr_config()
    result = export_project(run, cfg, _RECORDS, audit=_AUDIT, base_dir=tmp_path, day="2026-06-16",
                            gate=(True, []))
    project = Path(result["project_dir"])
    assert not result.get("export_blocked")
    assert result["n_components"] > 0
    assert (project / "markdown" / "99_full_manuscript.md").exists()


def test_export_gate_none_is_backward_compatible(tmp_path):
    """No gate supplied (legacy/programmatic caller) → unchanged full render."""
    run, cfg = _synthetic_run(), load_sr_config()
    result = export_project(run, cfg, _RECORDS, audit=_AUDIT, base_dir=tmp_path, day="2026-06-16")
    project = Path(result["project_dir"])
    assert not result.get("export_blocked")
    assert (project / "markdown" / "99_full_manuscript.md").exists()


def test_engine_export_gate_none_for_legacy_run_dir(tmp_path):
    """A run dir with no run_state.json is treated as ungated (returns None)."""
    from core.sr.orchestrator import engine
    assert engine.export_gate(tmp_path) is None


def test_engine_export_gate_blocks_unapproved_orchestrated_run(tmp_path):
    """A freshly created orchestrated run (all stages pending) must not be export-allowed."""
    from core.sr.orchestrator import engine
    cfg = load_sr_config()
    run_dir = engine.create_run(cfg, created_utc="2026-06-16T00:00:00Z", runs_dir=tmp_path)
    gate = engine.export_gate(run_dir)
    assert gate is not None
    ok, reasons = gate
    assert ok is False and reasons  # pending stages / unsigned gates block export


def test_engine_export_gate_fails_closed_on_corrupt_state(tmp_path):
    """A malformed run_state.json must produce a clean blocked decision, not a crash."""
    from core.sr.orchestrator import engine
    (tmp_path / "run_state.json").write_text("{ this is not valid json", encoding="utf-8")
    gate = engine.export_gate(tmp_path)
    assert gate is not None
    ok, reasons = gate
    assert ok is False and reasons
    assert any("corrupt" in r.lower() for r in reasons)


def test_engine_export_gate_fails_closed_when_state_deleted_but_gates_present(tmp_path):
    """If gate records exist but run_state.json is gone (tampering / downgrade attempt),
    refuse rather than silently treating the run as ungated legacy."""
    from core.sr.orchestrator import engine
    (tmp_path / "gates").mkdir()
    (tmp_path / "gates" / "assemble.json").write_text("{}", encoding="utf-8")
    gate = engine.export_gate(tmp_path)
    assert gate is not None
    ok, reasons = gate
    assert ok is False and reasons


def test_cli_main_refuses_and_purges_prior_dossier(tmp_path, capsys):
    """The export CLI must refuse (exit 2) AND purge any prior rendered dossier from the
    destination, so a stale downloadable manuscript can't survive a blocked re-export."""
    from core.sr.manuscript import __main__ as m
    from core.sr.manuscript.export import export_project
    from core.sr.orchestrator import engine
    cfg = load_sr_config()
    export_dir = tmp_path / "exports"
    # Pre-render an allowed dossier into the deterministic destination project dir.
    ok = export_project(_synthetic_run(), cfg, _RECORDS, audit=_AUDIT,
                        base_dir=export_dir, day="2026-06-16", gate=(True, []))
    project = Path(ok["project_dir"])
    assert (project / "markdown" / "99_full_manuscript.md").exists()
    assert (project / "index.html").exists()
    # An orchestrated, unapproved run exports to the same destination.
    run_dir = engine.create_run(cfg, created_utc="2026-06-16T00:00:00Z", runs_dir=tmp_path)
    (run_dir / "corpus.json").write_text("{}", encoding="utf-8")  # pass is-SR-run check
    with pytest.raises(SystemExit) as exc:
        m.main([str(run_dir), "--base-dir", str(export_dir), "--day", "2026-06-16"])
    assert exc.value.code == 2  # distinct from the exit-1 "not an SR run dir" case
    err = capsys.readouterr().err.lower()
    assert "blocked" in err or "gate" in err
    # Stale dossier purged; only the block notice + blocked manifest remain.
    assert not (project / "markdown" / "99_full_manuscript.md").exists()
    assert not (project / "index.html").exists()
    assert (project / "EXPORT_BLOCKED.md").exists()
    assert json.loads((project / "manifest.json").read_text())["export_blocked"] is True


def test_tool_path_refuses_and_purges_prior_dossier(tmp_path):
    """The registered `manuscript` tool path must enforce the gate too — refuse, write no
    manuscript.json, and purge any prior rendered dossier from the destination."""
    from core.sr.manuscript.export import export_project
    from core.sr.orchestrator import engine
    from core.tools.builtin import _run_manuscript
    cfg = load_sr_config()
    export_dir = tmp_path / "exports"
    ok = export_project(_synthetic_run(), cfg, _RECORDS, audit=_AUDIT,
                        base_dir=export_dir, day="2026-06-16", gate=(True, []))
    project = Path(ok["project_dir"])
    assert (project / "index.html").exists()
    run_dir = engine.create_run(cfg, created_utc="2026-06-16T00:00:00Z", runs_dir=tmp_path)
    (run_dir / "corpus.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        _run_manuscript(run_dir, {"base_dir": str(export_dir), "day": "2026-06-16"},
                        backend="claude")
    assert "gate" in str(exc.value).lower() or "blocked" in str(exc.value).lower()
    assert not (run_dir / "manuscript.json").exists()  # no artifact for a blocked run
    assert not (project / "index.html").exists()        # stale dossier purged
    assert not (project / "markdown" / "99_full_manuscript.md").exists()
    assert (project / "EXPORT_BLOCKED.md").exists()


def test_registered_tool_surface_refuses_before_input_artifacts_check(tmp_path):
    """The REAL registered surface (run_tool 'manuscript') must enforce the gate BEFORE
    its input-artifacts precheck — otherwise a blocked, incomplete run (missing
    screening_merged.json/extractions.json) raises FileNotFoundError and the block +
    purge never run."""
    import core.tools.builtin  # noqa: F401  (populates the tool registry)
    from core.sr.manuscript.export import export_project
    from core.sr.orchestrator import engine
    from core.tools.runner import run_tool
    cfg = load_sr_config()
    export_dir = tmp_path / "exports"
    ok = export_project(_synthetic_run(), cfg, _RECORDS, audit=_AUDIT,
                        base_dir=export_dir, day="2026-06-16", gate=(True, []))
    project = Path(ok["project_dir"])
    assert (project / "index.html").exists()
    run_dir = engine.create_run(cfg, created_utc="2026-06-16T00:00:00Z", runs_dir=tmp_path)
    # corpus.json present, but screening_merged.json / extractions.json deliberately ABSENT.
    (run_dir / "corpus.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:   # a clean gate block, NOT FileNotFoundError
        run_tool("manuscript", run_dir, {"base_dir": str(export_dir), "day": "2026-06-16"})
    assert "gate" in str(exc.value).lower() or "blocked" in str(exc.value).lower()
    assert not (run_dir / "manuscript.json").exists()
    assert not (project / "index.html").exists()        # stale dossier purged
    assert (project / "EXPORT_BLOCKED.md").exists()
