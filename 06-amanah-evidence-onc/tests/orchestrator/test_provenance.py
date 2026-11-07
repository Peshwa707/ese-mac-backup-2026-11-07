"""WP-M1.6 — run-dir reproducibility provenance."""
import json

from core.models import Citation, IdType, RetrievalResult
from core.sr.orchestrator import provenance as prov
from core.sr.orchestrator.state import RunState, StageState
from core.sr.pipeline import Corpus

TS = "2026-06-16T00:00:00Z"


def test_scrub_text_redacts_secrets():
    s = "model said sk-ABCDEF0123456789XYZ and Authorization: Bearer abcdef0123456789tok"
    out = prov.scrub_text(s)
    assert "sk-ABCDEF0123456789XYZ" not in out
    assert "REDACTED" in out


def test_append_llm_io_scrubs_and_appends(tmp_path):
    prov.append_llm_io(tmp_path, "screen", {"model": "claude", "raw": "key sk-DEADBEEF12345678"})
    prov.append_llm_io(tmp_path, "screen", {"model": "claude", "raw": "ok"})
    lines = (tmp_path / "llm_io" / "screen.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert "sk-DEADBEEF12345678" not in (tmp_path / "llm_io" / "screen.jsonl").read_text()


def test_environment_snapshot_has_keys():
    env = prov.environment_snapshot(timestamp=TS)
    for k in ("timestamp_utc", "git_sha", "git_dirty", "python", "platform", "packages", "r_version"):
        assert k in env
    assert env["python"].count(".") >= 1                  # looks like a version


def test_write_environment_under_run_dir(tmp_path):
    p = prov.write_environment(tmp_path, timestamp=TS)
    assert p == tmp_path / "environment.json" and p.exists()


def _corpus():
    from core.sr.config import load_sr_config
    cites = [Citation(id_type=IdType.PMID, id=p, source="pubmed", title="t", snippet="a",
                      verified=True, verification="live") for p in ("111", "222")]
    cfg = load_sr_config("mm-bcma-early-relapse")
    return Corpus(cfg=cfg, run_id="t",
                  search_result=RetrievalResult(source="pubmed", query="q", citations=cites),
                  identified=cites, deduped=cites, verified=cites, rejected=[],
                  seed_verification={}, per_source={"pubmed": 2}, sources_executed=["pubmed"])


def test_append_llm_io_redacts_secret_bearing_keys(tmp_path):
    prov.append_llm_io(tmp_path, "screen", {
        "api_key": "abcdef0123456789", "aws_secret_access_key": "ZZZ", "Authorization": "raw",
        "input_tokens": 1234, "model": "claude"})
    text = (tmp_path / "llm_io" / "screen.jsonl").read_text()
    assert "abcdef0123456789" not in text and "ZZZ" not in text
    rec = json.loads(text)
    assert rec["api_key"] == "[REDACTED]" and rec["aws_secret_access_key"] == "[REDACTED]"
    assert rec["input_tokens"] == 1234            # token COUNTS are not redacted
    assert rec["model"] == "claude"


def test_search_manifest_is_replay_grade(tmp_path):
    p = prov.write_search_manifest(tmp_path, _corpus(), timestamp=TS)
    m = json.loads(p.read_text())
    assert {r["id"] for r in m["verified"]} == {"111", "222"}
    first = m["verified"][0]
    assert {"id", "id_type", "source", "hash"} <= set(first)   # typed + hashed per record
    assert first["id_type"].lower() == "pmid" and len(first["hash"]) == 64
    assert len(m["frozen_set_hash"]) == 64
    assert m["counts"]["verified"] == 2


def test_write_provenance_records_stage_hashes(tmp_path):
    st = RunState(run_id="r", profile="systematic", created_utc=TS)
    st.stages["search"] = StageState(name="search", status="done",
                                     output_hashes={"corpus.json": "h"})
    p = prov.write_provenance(tmp_path, stage_log={"screening": {"model": "claude"}},
                              run_state=st, extras={"curation_drops": 3},
                              reproducibility={"search": "stochastic"}, timestamp=TS)
    rec = json.loads(p.read_text())
    assert rec["stage_models"] == {"screening": {"model": "claude"}}
    assert rec["stages"]["search"]["output_hashes"] == {"corpus.json": "h"}
    assert rec["stages"]["search"]["reproducibility"] == "stochastic"
    assert rec["curation_drops"] == 3


def test_legacy_provenance_writer_accepts_dir(tmp_path):
    from core.models import Provenance
    from core.render import provenance as legacy
    rid = legacy.write(Provenance(report_id="abc", queries=["q"], raw_results=[],
                                  verification_trail=[], rejected=[]), dir=tmp_path)
    assert rid == "abc"
    assert (tmp_path / "abc.provenance.json").exists()    # under run dir, not /tmp
