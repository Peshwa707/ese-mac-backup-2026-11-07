import base64
import hashlib
import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from bridge import BridgeError, EngineBridge
from server import (
    PACKAGE_ARTIFACT_NAMES,
    _ensure_offline_decisions,
    _locked_record_ids,
    _screening_state,
    _validate_extraction_artifact,
    _validate_rob_artifact,
    create_server,
)


def _b64(data: bytes) -> str:
    """Encode raw bytes the same way the browser upload path (app.js
    `readFileAsBase64`) does, for tests that need to exercise the actual
    `content_base64` wire contract rather than a plain-text convenience."""
    return base64.b64encode(data).decode("ascii")


def _object_hash(value) -> str:
    canonical = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


ENGINE_ROOT = Path("/Users/neurologic/Projects/evidence-synthesis-engine-screen-prove")

# The real, non-stubbed engine bridge is only exercised when this checkout is
# actually present locally (see
# test_stage_one_operator_shell_runs_against_real_engine_cli below). This
# mirrors the same existence check WebUIApp.health() already performs
# (`engine_root.is_dir()`); it does not read or depend on any file inside the
# engine checkout beyond that.
ENGINE_CHECKOUT_PRESENT = ENGINE_ROOT.is_dir()


def _request(server, path, method="GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _server(tmp_path):
    server = create_server(engine_root=ENGINE_ROOT, data_root=tmp_path / "runs", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _install_stub_bridge(
    monkeypatch,
    *,
    doctor_passed=True,
    doctor_returncode=0,
    doctor_stderr="",
    import_returncode=0,
    import_stderr="",
    import_write_output_before_failure=False,
    import_skip_output_on_success=False,
    extract_returncode=0,
    extract_stderr="",
    extract_skip_output_on_success=False,
    extract_output_mutation=None,
    rob_returncode=0,
    rob_stderr="",
    rob_skip_output_on_success=False,
    rob_output_mutation=None,
    synthesis_returncode=0,
    synthesis_stderr="",
    synthesis_skip_output_on_success=False,
    synthesis_output_mutation=None,
    dedup_returncode=0,
    dedup_stderr="",
    dedup_skip_output_on_success=False,
    lock_returncode=0,
    lock_stderr="",
    lock_skip_output_on_success=False,
    screen_returncode=0,
    screen_stderr="",
    screen_skip_output_on_success=False,
    screen_decisions=None,
    lock_record_ids=None,
    package_returncode=0,
    package_stderr="",
    package_skip_output_on_success=False,
    package_bad_manifest=False,
    replay_returncode=0,
    replay_stderr="",
    replay_skip_output_on_success=False,
    replay_mutations=None,
):
    """Replace EngineBridge._invoke -- the one place `run` and
    `run_diagnostic` actually shell out -- with a deterministic stand-in
    that writes the same artifact files the offline CLI would, without
    touching the real engine checkout. This package dispatches the `init`
    (via initialize_run), `doctor`, `import` (via import_records), `extract`
    (via extract_record), `rob` (via rob_assessment), `synthesize` (via
    synthesize), `dedup` (via dedup_records), `lock` (via lock_protocol),
    `screen` (via screen_records), `package` (via package_run), and `replay`
    (via replay_run) engine commands (see server.py VALID_ACTIONS, noting
    `adjudicate` and `human_adjudication` never reach the engine bridge at
    all), so this stub covers all eleven of those bridge-invoking commands,
    branch for branch, below. This keeps action tests fast, offline, and
    independent of the versioned engine bridge's internal CLI details.

    `dedup_returncode`/`dedup_stderr` and `lock_returncode`/`lock_stderr`
    let a test recreate a `dedup`/`lock` engine-command failure without
    touching the real engine checkout, mirroring `import_returncode`.

    `dedup_skip_output_on_success`/`lock_skip_output_on_success` mirror
    `import_skip_output_on_success`: a test can recreate a false-success
    engine invocation (exit 0 without producing the contracted artifact).

    `screen_returncode`/`screen_stderr`/`screen_skip_output_on_success`
    mirror the equivalent `dedup_*`/`lock_*` parameters for the `screen`
    command. `screen_decisions` is the list of `{"id": ..., "decision":
    ...}` objects the stub writes into `screen.json`'s `decisions` array
    -- unlike `dedup`/`lock` (whose stub output is a deterministic
    function of input byte length only), a test needs to control the
    actual decision values, including "abstain", to exercise stage 4's
    abstention/blocking-queue contract. Defaults to a fixed two-record,
    zero-abstention list when not given.

    The stub's `dedup` and `lock` outputs are deterministic functions of
    their inputs' exact byte lengths (not random or time-based), so two
    stub invocations against byte-identical input artifacts always produce
    byte-identical output artifacts -- this is what lets a test verify
    stage 3's "deterministic deduplication" contract via content hashes
    without depending on the real engine checkout.

    `doctor_passed`/`doctor_returncode`/`doctor_stderr` let a test recreate
    the real engine's diagnostic contract: `doctor` can write a structured
    report with `passed: false` *and* exit nonzero *and* emit stderr, all
    at once, exactly like an unrelated readiness warning (for example
    untracked planning files) does against the real engine.

    `import_returncode`/`import_stderr` let a test recreate an `import`
    engine-command failure (for example a malformed source file) without
    touching the real engine checkout.

    `import_write_output_before_failure` lets a test recreate the specific
    failure shape a real engine crash can take: the engine process writes a
    partial or fully-formed `frozen.json`/`raw/` output *and then* exits
    nonzero (for example a crash partway through writing the frozen
    artifact, after the output file was already created). Without this,
    the stub's failure path never actually left any output on disk, so it
    could not exercise the WebUI's failure-cleanup contract.

    `import_skip_output_on_success` lets a test recreate the inverse,
    false-success shape: the engine exits 0 (as if it succeeded) but never
    actually writes `frozen.json`/`raw/` (for example a CLI argument-shape
    drift, or a bug that no-ops instead of erroring). Without this, the
    stub's success path always wrote a real frozen artifact, so it could
    not exercise the WebUI's requirement to verify the artifact actually
    exists before trusting a zero exit code.

    `extract_returncode`/`extract_stderr`/`extract_skip_output_on_success`
    exercise the source-anchored extraction command's failure and
    false-success paths. `extract_output_mutation`, when supplied, mutates
    the stub payload before it recomputes the record hash, allowing tests to
    exercise WebUI-side contract validation against a well-hashed but
    malformed engine response.

    `rob_returncode`/`rob_stderr`/`rob_skip_output_on_success` and
    `rob_output_mutation` exercise the bounded risk-of-bias command's failure,
    false-success, and malformed-output paths.

    `synthesis_returncode`/`synthesis_stderr`/`synthesis_skip_output_on_success`
    and `synthesis_output_mutation` exercise the bounded single-study
    calculation's failure, false-success, and malformed-output paths.

    `lock_record_ids` is the list of record ids the stub's `lock` output
    reports as this run's locked record set (`lock.json`'s `records`
    array -- see `server.py` `_locked_record_ids`). When not given, it
    defaults to exactly the ids that appear in `decisions` (deduplicated,
    order preserved), so every existing test that only cares about
    `screen_decisions` gets a locked set that matches its screen output
    with full coverage by construction and does not need to be touched.
    Tests that specifically exercise the locked-record coverage check
    (a screen artifact missing a decision for a locked record, or
    reporting a decision for an id outside the locked set) pass this
    explicitly to create that mismatch.

    `package_returncode`/`package_stderr`/`package_skip_output_on_success`
    mirror the equivalent `screen_*` parameters for the `package` command.
    The stub's default `package` output writes both a `manifest` whose
    sha256 entries are computed directly from the run's actual on-disk
    `PACKAGE_ARTIFACT_NAMES` artifacts, so a default-configured package run
    is genuinely accurate, not merely well-shaped, *and* an
    `artifacts_base64` field carrying each of those same artifacts' exact
    bytes, base64-encoded. The latter is what makes `package.json` an
    actually portable package (docs/UI_BUILD_LOOP.md stage 5's "package
    bundles ... into a single portable package artifact" and "replay
    deterministically reproduces those same artifacts from that package"
    contracts) rather than a bare hash manifest that merely describes
    artifacts a replay would still have to source from elsewhere.
    `package_bad_manifest` lets a test recreate a well-shaped but *wrong*
    manifest (one entry replaced with a digest that does not match the real
    on-disk artifact), to exercise the WebUI's own manifest-vs-artifact
    verification.

    `replay_returncode`/`replay_stderr`/`replay_skip_output_on_success`
    mirror the same shape for `replay`. The stub's default `replay` output
    reconstructs each `PACKAGE_ARTIFACT_NAMES` artifact's bytes by
    base64-decoding that same `artifacts_base64` field read back out of the
    `package.json` file at `args[1]` -- *not* by reading any file still
    sitting in the run directory the package happens to live in -- and
    writes the result into `replay/`, so a default-configured replay is
    genuinely reproduced from the package artifact itself, not merely
    byte-stable because the original files were conveniently still on disk
    next to it (see `test_stub_replay_reconstructs_solely_from_package_content`,
    which deletes/moves those originals out from under a package/replay
    round trip through this same stub to prove exactly that).
    `replay_mutations` (a `{artifact_name: bytes}` mapping) lets a test
    recreate a non-reproducible replay: for any name present in it, the
    stub writes the given bytes under `replay/<name>` instead of decoding
    the package's embedded content -- letting a test exercise the "verify
    byte-stable offline replay" contract's failure path without needing a
    real non-deterministic engine."""

    decisions = (
        screen_decisions
        if screen_decisions is not None
        else [{"id": "rec-1", "decision": "include"}, {"id": "rec-2", "decision": "exclude"}]
    )
    locked_ids = (
        lock_record_ids
        if lock_record_ids is not None
        else list(
            dict.fromkeys(
                item.get("id")
                for item in decisions
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
        )
    )
    if not locked_ids:
        # Keep the stub's locked population non-empty even when a test is
        # deliberately asking the screen command to emit an empty decision
        # array; the failure under test is then the screen artifact, not an
        # invalid empty lock population.
        locked_ids = ["rec-1"]

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        command = args[0]

        def _value_after(flag):
            return args[args.index(flag) + 1]

        if command == "init":
            run_dir = Path(args[1])
            for name in ("protocol.json", "procedure.json", "schema.json"):
                (run_dir / name).write_text("{}\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "doctor":
            output = Path(_value_after("--output"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps({"passed": doctor_passed, "stub": command}) + "\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(
                args, doctor_returncode, stdout="", stderr=doctor_stderr
            )
        if command == "import":
            source_path = Path(args[1])
            source_label = _value_after("--source")
            output_path = Path(_value_after("--output"))
            raw_dir = Path(_value_after("--raw-dir"))
            if import_returncode != 0:
                if import_write_output_before_failure:
                    # Simulate a real engine crash that happens *after* it
                    # has already written (partial or complete) output,
                    # not merely a nonzero exit before it wrote anything.
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(
                        json.dumps({"stub": "partial-output-before-crash"}) + "\n",
                        encoding="utf-8",
                    )
                    (raw_dir / "raw.json").write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(
                    args, import_returncode, stdout="", stderr=import_stderr
                )
            if import_skip_output_on_success:
                # Simulate a false-success engine invocation: exit 0 without
                # producing the frozen artifact or raw directory it is
                # contracted to write.
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            raw_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "source_label": source_label,
                        "source_name": source_path.name,
                        "stub": command,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (raw_dir / "raw.json").write_text("{}\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "extract":
            source_path = Path(args[1])
            record_id = _value_after("--record-id")
            provenance_file = _value_after("--provenance-file")
            fields_path = Path(_value_after("--fields"))
            output_path = Path(_value_after("--output"))
            if extract_returncode != 0:
                return subprocess.CompletedProcess(
                    args, extract_returncode, stdout="", stderr=extract_stderr
                )
            if extract_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            source_bytes = source_path.read_bytes()
            full_text = source_bytes.decode("utf-8")
            source_hash = hashlib.sha256(source_bytes).hexdigest()
            fields = []
            for entry in json.loads(fields_path.read_text(encoding="utf-8")):
                status = entry.get("status", "reported")
                field = entry["field"]
                if status == "reported":
                    start, end = entry["start"], entry["end"]
                    quote = full_text[start:end]
                    fields.append(
                        {
                            "field": field,
                            "status": "reported",
                            "value": quote.strip(),
                            "span": {
                                "source_hash": source_hash,
                                "provenance_file": provenance_file,
                                "start": start,
                                "end": end,
                                "quote": quote,
                            },
                            "estimation_note": "",
                        }
                    )
                elif status == "not_reported":
                    fields.append(
                        {
                            "field": field,
                            "status": status,
                            "value": "",
                            "span": None,
                            "estimation_note": "",
                        }
                    )
                else:
                    fields.append(
                        {
                            "field": field,
                            "status": status,
                            "value": entry["value"],
                            "span": None,
                            "estimation_note": entry["estimation_note"],
                        }
                    )
            body = {
                "version": "extraction-record-v1",
                "record_id": record_id,
                "source_hash": source_hash,
                "provenance_file": provenance_file,
                "fields": fields,
            }
            if extract_output_mutation is not None:
                body = extract_output_mutation(body)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps({**body, "record_hash": _object_hash(body)}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "rob":
            spec_path = Path(args[1])
            output_path = Path(_value_after("--output"))
            if rob_returncode != 0:
                return subprocess.CompletedProcess(
                    args, rob_returncode, stdout="", stderr=rob_stderr
                )
            if rob_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            body = {
                "version": "rob-assessment-v1",
                "record_id": spec["record_id"].strip(),
                "tool": spec["tool"].strip(),
                "domains": [
                    {
                        "domain": entry["domain"].strip(),
                        "judgment": entry["judgment"],
                        "rationale": entry["rationale"].strip(),
                        "source_links": [link.strip() for link in entry.get("source_links", [])],
                    }
                    for entry in spec["domains"]
                ],
                "overall_judgment": spec["overall_judgment"],
            }
            if rob_output_mutation is not None:
                body = rob_output_mutation(body)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps({**body, "rob_hash": _object_hash(body)}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "synthesize":
            spec_path = Path(args[1])
            output_path = Path(_value_after("--output"))
            if synthesis_returncode != 0:
                return subprocess.CompletedProcess(
                    args, synthesis_returncode, stdout="", stderr=synthesis_stderr
                )
            if synthesis_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            counts = spec["counts"]
            a, n1 = counts["treated_events"], counts["treated_total"]
            b, n0 = counts["control_events"], counts["control_total"]
            measure = spec["measure"]
            flags = []
            if measure in ("risk_ratio", "odds_ratio") and (a == 0 or b == 0):
                flags.append("zero_event_cell_ratio_measure_undefined")
            if measure == "odds_ratio" and (a == n1 or b == n0):
                flags.append("zero_nonevent_cell_odds_ratio_undefined")
            if flags:
                point, ci_low, ci_high = None, None, None
                status = "insufficient_data"
            else:
                p1, p0 = a / n1, b / n0
                z = 1.959963984540054
                if measure == "risk_ratio":
                    point = p1 / p0
                    se = math.sqrt(1 / a - 1 / n1 + 1 / b - 1 / n0)
                    ci_low = math.exp(math.log(point) - z * se)
                    ci_high = math.exp(math.log(point) + z * se)
                elif measure == "odds_ratio":
                    point = (a * (n0 - b)) / (b * (n1 - a))
                    se = math.sqrt(1 / a + 1 / (n1 - a) + 1 / b + 1 / (n0 - b))
                    ci_low = math.exp(math.log(point) - z * se)
                    ci_high = math.exp(math.log(point) + z * se)
                else:
                    point = p1 - p0
                    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
                    ci_low = point - z * se
                    ci_high = point + z * se
                status = "calculated"
            body = {
                "version": "synthesis-calculation-v1",
                "record_id": spec["record_id"].strip(),
                "measure": measure,
                "status": status,
                "inputs": counts,
                "point_estimate": point,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "uncertainty_flags": flags,
            }
            if synthesis_output_mutation is not None:
                body = synthesis_output_mutation(body)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps({**body, "calculation_hash": _object_hash(body)}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "dedup":
            frozen_path = Path(args[1])
            output_path = Path(_value_after("--output"))
            if dedup_returncode != 0:
                return subprocess.CompletedProcess(
                    args, dedup_returncode, stdout="", stderr=dedup_stderr
                )
            if dedup_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {"stub": "dedup", "frozen_bytes_len": len(frozen_path.read_bytes())}
                )
                + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "lock":
            protocol_path = Path(_value_after("--protocol"))
            procedure_path = Path(_value_after("--procedure"))
            schema_path = Path(_value_after("--schema"))
            dedup_path = Path(_value_after("--input"))
            output_path = Path(_value_after("--output"))
            if lock_returncode != 0:
                return subprocess.CompletedProcess(
                    args, lock_returncode, stdout="", stderr=lock_stderr
                )
            if lock_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "stub": "lock",
                        "protocol_bytes_len": len(protocol_path.read_bytes()),
                        "procedure_bytes_len": len(procedure_path.read_bytes()),
                        "schema_bytes_len": len(schema_path.read_bytes()),
                        "dedup_bytes_len": len(dedup_path.read_bytes()),
                        "records": [{"id": record_id} for record_id in locked_ids],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "screen":
            input_path = Path(args[1])
            lock_path = Path(_value_after("--lock"))
            decisions_path = Path(_value_after("--decisions"))
            cache_dir = Path(_value_after("--cache-dir"))
            output_path = Path(_value_after("--output"))
            if screen_returncode != 0:
                return subprocess.CompletedProcess(
                    args, screen_returncode, stdout="", stderr=screen_stderr
                )
            if screen_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            assert input_path.is_file()
            assert decisions_path.is_file()
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "stub": "screen",
                        "lock_bytes_len": len(lock_path.read_bytes()),
                        "decisions": decisions,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "package":
            run_dir = Path(args[1]).parent
            output_path = Path(_value_after("--output"))
            if package_returncode != 0:
                return subprocess.CompletedProcess(
                    args, package_returncode, stdout="", stderr=package_stderr
                )
            if package_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            manifest = {
                name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
                for name in PACKAGE_ARTIFACT_NAMES
            }
            if package_bad_manifest:
                first_name = PACKAGE_ARTIFACT_NAMES[0]
                manifest[first_name] = "0" * 64
            # Embed each artifact's actual bytes (base64-encoded), not just
            # its digest, so `package.json` genuinely is a self-contained,
            # portable package: a later `replay` invocation can reproduce
            # every artifact from *this file alone*, without needing the
            # run directory's original files to still be present (see the
            # `replay` branch below and
            # `test_stub_replay_reconstructs_solely_from_package_content`).
            artifacts_base64 = {
                name: base64.b64encode((run_dir / name).read_bytes()).decode("ascii")
                for name in PACKAGE_ARTIFACT_NAMES
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "stub": "package",
                        "manifest": manifest,
                        "artifacts_base64": artifacts_base64,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "replay":
            package_path = Path(args[1])
            output_dir = package_path.parent / "replay"
            if replay_returncode != 0:
                return subprocess.CompletedProcess(
                    args, replay_returncode, stdout="", stderr=replay_stderr
                )
            if replay_skip_output_on_success:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            # Reconstruct every artifact's bytes solely from the package
            # artifact's own embedded content -- never from any file still
            # sitting in the run directory the package happens to live in
            # (deliberately not even referencing `package_path.parent`) --
            # so this stub actually exercises "reproduces those same
            # artifacts from that package" (docs/UI_BUILD_LOOP.md stage 5),
            # not merely "reproduces whatever is still on disk next to it".
            package_payload = json.loads(package_path.read_text(encoding="utf-8"))
            artifacts_base64 = package_payload.get("artifacts_base64")
            if not isinstance(artifacts_base64, dict):
                raise AssertionError(
                    "stub package artifact is missing its embedded 'artifacts_base64' "
                    "content; replay cannot reconstruct from it"
                )
            output_dir.mkdir(parents=True, exist_ok=True)
            mutations = replay_mutations or {}
            for name in PACKAGE_ARTIFACT_NAMES:
                data = mutations.get(name)
                if data is None:
                    encoded = artifacts_base64.get(name)
                    if encoded is None:
                        raise AssertionError(
                            f"stub package artifact has no embedded content for '{name}'"
                        )
                    data = base64.b64decode(encoded)
                (output_dir / name).write_bytes(data)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected stub engine command: {command}")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)


def test_health_is_offline_only(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, payload = _request(server, "/api/health")
        assert status == 200
        assert payload["ok"] is True
        assert payload["offline_only"] is True
        assert payload["credentials_loaded"] is False
        assert payload["live_connectors"] is False
    finally:
        server.shutdown()
        thread.join()


def test_create_run_initializes_engine_contract(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, run = _request(server, "/api/runs", "POST", {"name": "Fixture review"})
        assert status == 201
        assert run["name"] == "Fixture review"
        run_dir = tmp_path / "runs" / run["id"]
        for filename in ("protocol.json", "procedure.json", "schema.json", "run.json"):
            assert (run_dir / filename).is_file()
        _, listing = _request(server, "/api/runs")
        assert listing["runs"][0]["id"] == run["id"]
    finally:
        server.shutdown()
        thread.join()


def test_screening_state_accepts_results_shaped_screen_artifact(tmp_path):
    """`_screening_state` (server.py) has a documented fallback (lines
    544-550 at the time this test was added) for a `screen.json` shaped with
    a top-level `results` array of `{"record_id": ..., "decision": ...}`
    objects rather than this WebUI's own `decisions` array of `{"id": ...,
    "decision": ...}` objects. This is a genuine "Compatibility" (stage 6)
    concern: nothing in `docs/UI_BUILD_LOOP.md`'s own description of stage
    4/5's screening contract, and nothing in this file's deterministic
    `_install_stub_bridge` stub (which always writes `screen.json`'s
    `decisions` array directly -- see the `command == "screen"` branch
    above), ever constructs a `results`-shaped artifact, so before this
    test, that fallback branch had no test coverage anywhere in this
    package's always-run offline suite; only a real engine whose actual
    `screen` command happens to emit `results` instead of `decisions` would
    have exercised it at all, and only then via the isolated-checkout-gated
    real-engine tests this session cannot run or even inspect the checkout
    for. A regression that broke this fallback (for example a typo in
    `record_id`/`decision` key lookup, or accidentally requiring `decisions`
    to be present too) could have silently shipped without any offline test
    catching it.

    This test calls `_screening_state` directly against a hand-built run
    directory (a `lock.json` with a locked two-record set and a
    `results`-shaped `screen.json`, no bridge or HTTP server involved) and
    asserts the fallback normalizes it into the same `decisions`/
    `abstained_ids`/`remaining` shape a `decisions`-shaped artifact would
    produce, closing this narrow offline-coverage gap without changing
    `server.py`, `bridge.py`, or any other non-test source file."""
    (tmp_path / "lock.json").write_text(
        json.dumps({"records": [{"id": "rec-1"}, {"id": "rec-2"}]}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "screen.json").write_text(
        json.dumps(
            {
                "results": [
                    {"record_id": "rec-1", "decision": "include"},
                    {"record_id": "rec-2", "decision": "abstain"},
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    state = _screening_state(tmp_path)
    assert state["decisions"] == [
        {"id": "rec-1", "decision": "include"},
        {"id": "rec-2", "decision": "abstain"},
    ]
    assert state["abstained_ids"] == ["rec-2"]
    assert state["adjudications"] == {}
    assert state["remaining"] == ["rec-2"]


def test_locked_record_ids_falls_back_to_dedup_artifact_when_lock_records_is_empty(tmp_path):
    """`_locked_record_ids` (server.py) has its own documented compatibility
    fallback, the same kind `_screening_state`'s `results`-shaped
    `screen.json` handling above already has a dedicated unit test for: a
    `lock.json` whose own `records` array is missing or empty does not fail
    closed immediately -- it falls back to this run's `dedup.json` artifact,
    reading that artifact's `corpus_hash`/`records` (keyed by `record_id`,
    not this WebUI's own `id`), provided `lock.json`'s `input_hash` actually
    matches that `dedup.json`'s `corpus_hash`. This is a genuine
    "Compatibility" (stage 6) concern: it exists so a real engine `lock`
    command that does not shape its output exactly like this WebUI's own
    `records`/`id` contract can still be trusted, as long as it can be tied
    back to a known dedup artifact.

    Before this test, that fallback branch had no coverage anywhere in this
    package's always-run offline suite: `_install_stub_bridge`'s own
    `command == "lock"` branch always writes a full, non-empty `records`
    array of `{"id": ...}` objects directly (see the stub's `lock` branch
    above), so no stub-driven HTTP test can ever reach this fallback, and
    `_locked_record_ids` itself was never imported for direct unit testing
    the way `_screening_state` already is. Only a real engine whose actual
    `lock` command happens to omit `records` would have exercised this at
    all, and only then via the isolated-checkout-gated real-engine tests
    this session cannot run or even inspect the checkout for. A regression
    that broke this fallback (for example comparing the wrong hash fields,
    or reading `id` instead of `record_id` from the dedup artifact) could
    have silently shipped without any offline test catching it.

    This test calls `_locked_record_ids` directly against a hand-built pair
    of `lock.json`/`dedup.json` files (no bridge or HTTP server involved)
    and asserts the fallback returns exactly the record ids `dedup.json`
    reports, closing this narrow offline-coverage gap without changing
    `server.py`, `bridge.py`, or any other non-test source file."""
    dedup_path = tmp_path / "dedup.json"
    dedup_path.write_text(
        json.dumps(
            {
                "corpus_hash": "corpus-hash-value",
                "records": [{"record_id": "rec-1"}, {"record_id": "rec-2"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(
        json.dumps({"input_hash": "corpus-hash-value", "records": []}) + "\n",
        encoding="utf-8",
    )

    assert _locked_record_ids(lock_path, dedup_path) == {"rec-1", "rec-2"}


def test_locked_record_ids_fallback_rejects_input_hash_mismatch(tmp_path):
    """The other half of that same fallback's contract, mirroring this
    file's existing "necessary but not sufficient" posture for every other
    artifact: if `lock.json`'s own `input_hash` does not match `dedup.json`'s
    `corpus_hash`, the fallback must fail closed instead of silently
    trusting a dedup artifact that does not actually correspond to this lock
    artifact. Without this check, a stale or unrelated `dedup.json` left on
    disk (for example from a previous, already-superseded `dedup` run) could
    be silently substituted for the locked record population `lock.json`
    itself never actually named."""
    dedup_path = tmp_path / "dedup.json"
    dedup_path.write_text(
        json.dumps(
            {"corpus_hash": "corpus-hash-value", "records": [{"record_id": "rec-1"}]}
        )
        + "\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(
        json.dumps({"input_hash": "does-not-match", "records": []}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BridgeError, match="input_hash"):
        _locked_record_ids(lock_path, dedup_path)


def test_ensure_offline_decisions_uses_dedup_artifact_record_ids_when_present(tmp_path):
    """`_ensure_offline_decisions` (server.py) has its own documented
    primary path, distinct from the `_locked_record_ids`-based fallback
    exercised by the two tests immediately above: when this run's
    `dedup.json` artifact itself contains a non-empty `records` array of
    `{"record_id": ...}` objects, that array -- not `lock.json` -- is the
    source of the locked record population `screen` defaults every record
    to an explicit "abstain" against. This is a genuine "Compatibility"
    (stage 6) concern in exactly the same way the two fallback tests above
    already are: it exists so a real engine `dedup` command's own
    `record_id`-keyed `records` output can be trusted directly, without
    requiring a round trip through `lock.json`'s differently-keyed `id`
    shape first.

    Before this test, this branch had no coverage anywhere in this
    package's always-run offline suite for the opposite reason the two
    fallback tests above document: `_install_stub_bridge`'s own
    `command == "dedup"` branch writes only `{"stub": "dedup",
    "frozen_bytes_len": ...}` -- it never writes a `records` field at all
    -- so every existing stub-driven `screen` test actually falls straight
    through to `_ensure_offline_decisions`'s *fallback* branch
    (`_locked_record_ids(lock_path, dedup_path)`, reading `lock.json`'s own
    `records` array) without ever exercising the primary, dedup-artifact-
    keyed branch this test targets. `_ensure_offline_decisions` itself was
    never imported for direct unit testing the way `_locked_record_ids` and
    `_screening_state` already are. A regression that broke this branch
    (for example reading `id` instead of `record_id`, or silently falling
    through to the `lock.json`-based fallback even when `dedup.json`'s own
    `records` array is present and well-formed) could have shipped without
    any offline test catching it.

    This test calls `_ensure_offline_decisions` directly against a
    hand-built `dedup.json`/`lock.json` pair (no bridge or HTTP server
    involved) whose two record populations deliberately disagree --
    `dedup.json` reports `rec-1`/`rec-2`; `lock.json` reports the
    unrelated, non-empty `rec-9` -- so the resulting `decisions.json` can
    only match `dedup.json`'s own records if the primary branch, not the
    fallback, was actually used. `lock.json`'s own `records` array is
    deliberately left non-empty and well-formed specifically so
    `_locked_record_ids` would happily resolve *without* ever consulting
    `dedup.json`, if the fallback were wrongly taken instead -- proving
    this test would actually fail, not vacuously pass, under that
    regression."""
    dedup_path = tmp_path / "dedup.json"
    dedup_path.write_text(
        json.dumps(
            {
                "corpus_hash": "corpus-hash-value",
                "records": [{"record_id": "rec-1"}, {"record_id": "rec-2"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(
        json.dumps({"input_hash": "corpus-hash-value", "records": [{"id": "rec-9"}]}) + "\n",
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.json"

    _ensure_offline_decisions(dedup_path, lock_path, decisions_path)

    assert decisions_path.is_file()
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    assert decisions == {"rec-1": "abstain", "rec-2": "abstain"}


def test_ensure_offline_decisions_rejects_dedup_artifact_with_repeated_record_id(tmp_path):
    """The other half of that same primary branch's contract, mirroring
    this file's existing "necessary but not sufficient" posture for every
    other artifact: a `dedup.json` whose `records` array reports the same
    `record_id` twice must fail closed instead of silently collapsing two
    distinct dedup records into a single default-abstain decision, which
    would otherwise leave `screen`'s "one decision per record" contract
    (docs/UI_BUILD_LOOP.md stage 4) resolvable against a locked population
    smaller than the run's own deduplicated corpus actually reports."""
    dedup_path = tmp_path / "dedup.json"
    dedup_path.write_text(
        json.dumps(
            {
                "corpus_hash": "corpus-hash-value",
                "records": [{"record_id": "rec-1"}, {"record_id": "rec-1"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(
        json.dumps({"input_hash": "corpus-hash-value", "records": [{"id": "rec-9"}]}) + "\n",
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.json"

    with pytest.raises(BridgeError, match="repeats record id"):
        _ensure_offline_decisions(dedup_path, lock_path, decisions_path)
    assert not decisions_path.is_file()


def test_static_shell_panel_headings_are_sequentially_numbered():
    """Regression test for a found drift in `static/index.html`'s own
    operator-shell narrative: its panel headings (`<h2>N. ...</h2>`) are
    meant to number the operator's stage-by-stage workflow sequentially, but
    "5. Import source records" and "5. Deduplicate & lock" were found
    duplicating the same step number, and every panel heading after those
    two was consequently numbered one step behind what its own heading text
    implied ("6. Screen & adjudicate" appearing before "7. Extract
    source-anchored data", "10. Package & replay" before "11. Artifacts",
    and so on) -- an operator reading only the panel numbers, not this
    document or the underlying action wiring, would be misled about how
    many steps this workflow actually has and in what order they appear.

    This parses `static/index.html` directly (no server involved, the same
    read-only, non-executing posture every other mechanical guard in this
    file already uses for its own source) and asserts every numbered panel
    heading reads 1, 2, 3, ... in order with no duplicate or skipped number,
    so a future edit that inserts, removes, or misnumbers a panel fails this
    test immediately instead of silently drifting until an operator (or the
    next manual audit) notices by hand."""
    import re

    html_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    headings = re.findall(r"<h2>(\d+)\.[^<]*</h2>", html)
    assert headings, "expected at least one numbered '<h2>N. ...</h2>' panel heading in static/index.html"
    numbers = [int(value) for value in headings]
    assert numbers == list(range(1, len(numbers) + 1)), (
        "static/index.html panel headings must be numbered sequentially with no "
        f"duplicate or skipped step number, got {numbers}"
    )


def test_static_shell_is_served(tmp_path):
    server, thread = _server(tmp_path)
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/")
        with urlopen(request) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Offline Workbench" in body
        # This is a stage-3 package (docs/UI_BUILD_LOOP.md stage 3:
        # deduplication and lock): the shell must actually expose controls
        # for both new actions, not only the server-side endpoints.
        assert 'data-action="dedup"' in body
        assert 'data-action="lock"' in body
        # This is a stage-4 package (docs/UI_BUILD_LOOP.md stage 4:
        # screening): the shell must also expose a control for running
        # cached screening and an explicit on/off toggle for human
        # adjudication -- "adjudicate" itself has no static control since
        # it always targets a specific, dynamically-rendered queue entry.
        assert 'data-action="screen"' in body
        assert 'id="human-adjudication-toggle"' in body
        # This is a stage-5 package (docs/UI_BUILD_LOOP.md stage 5: replay
        # and audit): the shell must also expose controls for bundling a
        # run's stage artifacts into a package and replaying that package.
        assert 'data-action="package"' in body
        assert 'data-action="replay"' in body
        # Stage 7 (docs/UI_BUILD_LOOP.md): source-anchored extraction must be
        # reachable from the operator shell.
        assert 'data-action="extract"' in body
        # Stage 8 (docs/UI_BUILD_LOOP.md): bounded risk-of-bias assessment
        # must also be reachable from the operator shell.
        assert 'data-action="rob"' in body
        # Stage 9 (docs/UI_BUILD_LOOP.md): bounded single-study synthesis.
        assert 'data-action="synthesize"' in body

        # Regression test for a found drift: the shell's own "Offline
        # boundary" summary paragraph (the prose immediately below the
        # heading, distinct from -- and read before -- any individual
        # stage's own dedicated panel further down the page) is supposed to
        # describe every stage this package actually implements, the same
        # way docs/UI_BUILD_LOOP.md's "Current package status" section is
        # required to. That paragraph was found still saying "stage-8
        # offline package" and omitting stage 9 (single-study synthesis)
        # entirely, even though stage 9's own dedicated panel and
        # `data-action="synthesize"` control (asserted above) were already
        # present and reachable. A dedicated stage panel being reachable is
        # not the same claim as the shell's own top-level summary of what it
        # implements being accurate -- an operator reading only that summary
        # paragraph would not learn synthesis exists at all. This asserts
        # the summary paragraph mentions synthesis so this specific drift
        # cannot silently reoccur.
        summary_start = body.index("Offline boundary")
        summary_paragraph = body[summary_start : body.index("</p>", summary_start)]
        assert "synthesis" in summary_paragraph.lower(), summary_paragraph
        assert "stage-8" not in summary_paragraph, summary_paragraph
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason="isolated offline engine checkout not present locally",
)
def test_stage_six_full_pipeline_runs_against_real_engine_cli(tmp_path):
    """docs/UI_BUILD_LOOP.md stage 6 ("Compatibility"): the pinned
    engine-artifact contract check stage 6 requires. This drives this
    package's entire scope -- run creation, `import`, `dedup`, `lock`,
    `screen` (resolving via `adjudicate` if the real engine's cached
    screening result happens to abstain), `package`, and `replay` --
    through the real, un-stubbed `EngineBridge` against the isolated
    offline engine checkout, exactly as `server.py` invokes it in
    production.

    Unlike every other test in this file, this test deliberately never
    calls `_install_stub_bridge`: `_server` already constructs
    `create_server(engine_root=ENGINE_ROOT, ...)` (see `_server` above),
    and leaving `EngineBridge._invoke` unpatched means every action below
    actually shells out to `python -m screen_prove.cli` inside the
    isolated engine checkout, the same way this WebUI always invokes it in
    production.

    It is skipped (not failed) when the isolated engine checkout is not
    present locally (`ENGINE_CHECKOUT_PRESENT`), never reads or modifies
    anything inside that checkout, and uses no stub, no network access,
    and no credentials. This package's own offline test suite (every
    deterministic-stub test in this file) does not depend on it."""
    server, thread = _server(tmp_path)
    try:
        _, run = _request(
            server, "/api/runs", "POST", {"name": "Stage six compatibility"}
        )
        run_id = run["id"]
        assert run["stages"]["initialized"] == "complete", run

        # A conventional, standard-shaped RIS record: this test's purpose is
        # to pin the WebUI<->engine CLI/artifact contract (docs/UI_BUILD_LOOP.md
        # stage 6), not to exercise RIS-parsing edge cases -- those remain
        # stage 2's job, already covered offline via the deterministic stub.
        raw = (
            b"TY  - JOUR\r\n"
            b"TI  - A minimal fixture record for compatibility testing\r\n"
            b"AU  - Doe, Jane\r\n"
            b"PY  - 2024\r\n"
            b"JO  - Journal of Fixture Studies\r\n"
            b"AB  - A short abstract used only to exercise the real offline engine CLI.\r\n"
            b"ER  -  \r\n"
        )
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "compat.ris", "content_base64": _b64(raw)},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload

        # Turn human adjudication on *before* screening so that, if the real
        # engine's cached screening result happens to abstain on this
        # fixture record, the run's "screen" stage genuinely blocks and this
        # test can exercise "adjudicate" resolving it -- mirroring stage 4's
        # own "abstention and blocking queue" contract, not merely stage 6's
        # pipeline shape.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] in ("complete", "blocked"), payload

        if payload["run"]["stages"]["screen"] == "blocked":
            for record_id in list(payload["screen"]["remaining"]):
                status, payload = _request(
                    server,
                    f"/api/runs/{run_id}/adjudicate",
                    "POST",
                    {"id": record_id, "decision": "include"},
                )
                assert status == 200, payload
            assert payload["run"]["stages"]["screen"] == "complete", payload

        status, package = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, package
        assert package["run"]["stages"]["package"] == "complete", package
        manifest = package["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), package

        status, replay = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, replay
        assert replay["run"]["stages"]["replay"] == "complete", replay
        assert replay["replay"]["byte_stable"] is True, replay
    finally:
        server.shutdown()
        thread.join()


def test_supported_run_actions_are_exposed_and_gated(
    tmp_path, monkeypatch
):
    """This stage-9 package (docs/UI_BUILD_LOOP.md stage 9: synthesis)
    exposes the offline run actions beyond creation/listing: the
    offline `doctor` diagnostic (stage 1), `import` (stage 2), `dedup` and
    `lock` (stage 3), `screen`, `adjudicate`, and `human_adjudication`
    (stage 4), and now `package` and `replay` (stage 5). Any other action
    name must still be a plain 404, not merely gated behind a
    prerequisite."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Scope check"})
        run_id = run["id"]
        assert run["stages"]["initialized"] == "complete"
        assert run["human_adjudication"] == "off"

        status, payload = _request(server, f"/api/runs/{run_id}/unknown-action", "POST", {})
        assert status == 404, payload
        assert payload["error"] == "unsupported action"

        # "package", "replay", "extract", "rob", and "synthesize" are all
        # reachable (not 404) even though there is nothing to
        # package/replay/extract/assess/synthesize yet -- they are
        # correctly gated (409), not missing. "extract" was previously
        # absent from this list even though "rob" and "synthesize" (both of
        # which sit *downstream* of "extract" in ACTION_PREREQUISITES) were
        # already checked here: a regression that made "extract" wrongly
        # return 404 (dropped from VALID_ACTIONS) or wrongly return 200
        # (its "screen" prerequisite silently stopped being enforced) could
        # have slipped past this test even though its two downstream
        # actions' own gating stayed correct.
        for action in ("package", "replay", "extract", "rob", "synthesize"):
            status, payload = _request(server, f"/api/runs/{run_id}/{action}", "POST", {})
            assert status == 409, (action, payload)
            assert payload["error"] != "unsupported action", payload

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete"

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "scope.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete"

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete"

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete"

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        # "adjudicate" is reachable (not a 404) even though there is
        # nothing to adjudicate yet -- it is correctly gated (409), not
        # missing.
        status, payload = _request(
            server, f"/api/runs/{run_id}/adjudicate", "POST", {"id": "rec-1", "decision": "include"}
        )
        assert status == 409, payload
        assert payload["error"] != "unsupported action", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload
        assert payload["replay"]["byte_stable"] is True, payload
    finally:
        server.shutdown()
        thread.join()


def _screened_run(server, name="Extraction"):
    _, run = _request(server, "/api/runs", "POST", {"name": name})
    run_id = run["id"]
    status, payload = _request(
        server,
        f"/api/runs/{run_id}/import",
        "POST",
        {
            "filename": "records.ris",
            "content_base64": _b64(
                b"TY  - JOUR\nTI  - A minimal fixture record\nAU  - Doe, Jane\nPY  - 2024\nER  -\n"
            ),
        },
    )
    assert status == 200, payload
    for action in ("dedup", "lock", "screen"):
        status, payload = _request(server, f"/api/runs/{run_id}/{action}", "POST", {})
        assert status == 200, (action, payload)
    return run_id


def _extraction_body(source: bytes, record_id="rec-1") -> dict:
    text = source.decode("utf-8")
    quote = "240 participants"
    start = text.index(quote)
    return {
        "record_id": record_id,
        "filename": "study-fulltext.txt",
        "content_base64": _b64(source),
        "fields": [
            {"field": "sample_size", "status": "reported", "start": start, "end": start + len(quote)},
            {"field": "blinding_method", "status": "not_reported"},
            {
                "field": "half_sample",
                "status": "estimated",
                "value": "120",
                "estimation_note": "240 participants / 2",
            },
        ],
    }


def _rob_body(record_id="rec-1") -> dict:
    return {
        "record_id": record_id,
        "tool": "protocol-rob-v1",
        "overall_judgment": "some_concerns",
        "domains": [
            {
                "domain": "randomization",
                "judgment": "low_risk",
                "rationale": "The supplied methods fixture describes sequence generation.",
                "source_links": ["Methods p.2"],
            },
            {
                "domain": "missing_outcome_data",
                "judgment": "not_assessed",
                "rationale": "The supplied source does not report this domain.",
                "source_links": [],
            },
        ],
    }


def _synthesis_body(record_id="rec-1", measure="risk_difference") -> dict:
    return {
        "record_id": record_id,
        "measure": measure,
        "counts": {
            "treated_events": 2,
            "treated_total": 10,
            "control_events": 1,
            "control_total": 10,
        },
    }


def _extracted_run(server, name="Risk of bias"):
    run_id = _screened_run(server, name=name)
    source = b"Sample size was 240 participants.\n"
    status, payload = _request(
        server, f"/api/runs/{run_id}/extract", "POST", _extraction_body(source)
    )
    assert status == 200, payload
    return run_id


def _robbed_run(server, name="Synthesis"):
    run_id = _extracted_run(server, name=name)
    status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
    assert status == 200, payload
    return run_id


def test_extract_requires_completed_screening(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Extraction gate"})
        status, payload = _request(
            server,
            f"/api/runs/{run['id']}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.\n"),
        )
        assert status == 409, payload
        assert "screen" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_extract_preserves_source_and_validates_auditable_fields(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server, f"/api/runs/{run_id}/extract", "POST", _extraction_body(source)
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete", payload
        assert payload["extraction"]["field_counts"] == {
            "estimated": 1,
            "not_reported": 1,
            "reported": 1,
        }
        assert len(payload["extraction"]["record_hash"]) == 64

        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "extraction" / "source" / "study-fulltext.txt").read_bytes() == source
        artifact = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        assert artifact["fields"][0]["status"] == "reported"
        assert artifact["fields"][1]["value"] == ""
        assert artifact["fields"][2]["estimation_note"] == "240 participants / 2"
        assert "extraction.json" in {item["name"] for item in payload["artifacts"]}
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("record_id", ["unknown-record", "rec-2"])
def test_extract_rejects_unknown_or_excluded_screened_record(tmp_path, monkeypatch, record_id):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.\n", record_id=record_id),
        )
        assert status == 400, payload
        if record_id == "unknown-record":
            assert "not present" in payload["error"], payload
        else:
            assert "final include" in payload["error"], payload
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["extraction"] == "pending"
        assert not (tmp_path / "runs" / run_id / "extraction").exists()
    finally:
        server.shutdown()
        thread.join()


def test_extract_accepts_human_resolved_include_decision(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, screen_decisions=[{"id": "rec-1", "decision": "abstain"}])
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Adjudicated extraction"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.", record_id="rec-1"),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete"
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize(
    "mutation, expected_error",
    [
        (
            lambda body: {**body, "fields": body["fields"][:-1]},
            "field count",
        ),
        (
            lambda body: {
                **body,
                "fields": [
                    {
                        **body["fields"][0],
                        "field": "invented_field",
                    },
                    *body["fields"][1:],
                ],
            },
            "unique non-empty names",
        ),
    ],
)
def test_extract_rejects_output_that_does_not_match_requested_fields(
    tmp_path, monkeypatch, mutation, expected_error
):
    _install_stub_bridge(monkeypatch, extract_output_mutation=mutation)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.\n"),
        )
        assert status == 400, payload
        assert expected_error in payload["error"], payload
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["extraction"] == "pending"
        assert not (tmp_path / "runs" / run_id / "extraction.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_extract_rejects_empty_or_whitespace_only_reported_span(tmp_path, monkeypatch):
    def mutate(body):
        fields = [dict(entry) for entry in body["fields"]]
        fields[0] = {
            **fields[0],
            "value": " ",
            "span": {**fields[0]["span"], "start": 0, "end": 0, "quote": ""},
        }
        return {**body, "fields": fields}

    _install_stub_bridge(monkeypatch, extract_output_mutation=mutate)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.\n"),
        )
        assert status == 400, payload
        assert "invalid source span" in payload["error"], payload
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("action", ["import", "dedup", "lock", "screen"])
def test_upstream_rerun_invalidates_completed_extraction(tmp_path, monkeypatch, action):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.\n"),
        )
        assert status == 200, payload
        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "extraction.json").is_file()

        rerun_payload = (
            {
                "filename": "records-rerun.ris",
                "content_base64": _b64(
                    b"TY  - JOUR\nTI  - Re-run fixture\nAU  - Doe, Jane\nPY  - 2025\nER  -\n"
                ),
            }
            if action == "import"
            else {}
        )
        status, payload = _request(server, f"/api/runs/{run_id}/{action}", "POST", rerun_payload)
        assert status == 200, (action, payload)
        assert payload["run"]["stages"]["extraction"] == "pending", (action, payload)
        assert not (run_dir / "extraction.json").exists()
        assert not (run_dir / "extraction").exists()
    finally:
        server.shutdown()
        thread.join()


def test_extract_failure_never_marks_stage_complete_or_leaves_record_artifact(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, extract_returncode=7, extract_stderr="extract failed")
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server, f"/api/runs/{run_id}/extract", "POST", _extraction_body(source)
        )
        assert status == 400, payload
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["extraction"] == "pending"
        assert not (tmp_path / "runs" / run_id / "extraction.json").exists()
        assert (tmp_path / "runs" / run_id / "extraction" / "source" / "study-fulltext.txt").read_bytes() == source
    finally:
        server.shutdown()
        thread.join()


def test_extract_false_success_is_rejected(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, extract_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants.\n"),
        )
        assert status == 400, payload
        assert "did not produce" in payload["error"]
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["extraction"] == "pending"
    finally:
        server.shutdown()
        thread.join()


def test_extract_rejects_non_utf8_source_before_persisting_it(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            {
                    "record_id": "rec-1",
                "filename": "study.txt",
                "content_base64": _b64(b"\xff\xfe"),
                "fields": [{"field": "sample_size", "status": "not_reported"}],
            },
        )
        assert status == 400, payload
        assert "UTF-8" in payload["error"]
        assert not (tmp_path / "runs" / run_id / "extraction").exists()
    finally:
        server.shutdown()
        thread.join()


def test_invalid_extraction_retry_invalidates_prior_success(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server)
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server, f"/api/runs/{run_id}/extract", "POST", _extraction_body(source)
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete"

        invalid = {
            "record_id": "study-1",
            "filename": "study.txt",
            "content_base64": _b64(b"\xff\xfe"),
            "fields": [{"field": "sample_size", "status": "not_reported"}],
        }
        status, payload = _request(
            server, f"/api/runs/{run_id}/extract", "POST", invalid
        )
        assert status == 400, payload
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["extraction"] == "pending"
        assert not (tmp_path / "runs" / run_id / "extraction.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_validate_extraction_artifact_fails_closed_on_non_finite_hash_body(tmp_path):
    """`_validate_extraction_artifact`'s (server.py) own `record_hash`
    re-verification -- `record_hash != _object_hash(body)` -- calls
    `_object_hash`, which serializes `body` with `allow_nan=False` (the
    engine's own canonical hash contract), with no guard against a `body`
    that cannot be serialized that way. `_validate_rob_artifact`'s sibling
    `rob_hash` check has the same gap (see the next test below);
    `_validate_synthesis_artifact`'s own `calculation_hash` check already
    wraps its `_object_hash` call in `try`/`except (TypeError, ValueError)`
    (see server.py), but neither `_validate_extraction_artifact` nor
    `_validate_rob_artifact` shared that guard until now. A `body`
    containing a non-finite float (`NaN`/`Infinity`, which `json.loads`
    accepts by default even though `json.dumps(..., allow_nan=False)` --
    the canonical hash's own contract -- rejects it) anywhere among its
    *unvalidated* top-level fields -- this WebUI only inspects specific
    known keys (`version`, `record_id`, `source_hash`, `provenance_file`,
    `fields`); any additional engine-supplied top-level field simply rides
    along inside `body` untouched by every earlier check -- would raise an
    uncaught `TypeError`/`ValueError` instead of honoring this function's
    own documented "Fails closed (raises `BridgeError`)" contract. This is
    a genuine "Compatibility" (docs/UI_BUILD_LOOP.md stage 6) concern:
    engine-produced JSON is untrusted input this compatibility-adapter
    layer must fail closed against with its own documented exception type,
    not merely something that happens to still resolve to a 400 through an
    unrelated, broader exception clause elsewhere in this file.

    Nothing in this package's always-run offline suite previously exercised
    this: every existing malformed-extraction-output test drives the
    HTTP `extract` action through `_install_stub_bridge`'s
    `extract_output_mutation` hook, which recomputes `record_hash` itself
    from the mutated body via the *same* `allow_nan=False` helper this
    file's own `_object_hash` mirrors -- so a mutation that introduced a
    non-finite float would make the stub's own hash computation raise
    before ever reaching the server-side validator this test targets, and
    `_validate_extraction_artifact` itself was never imported for direct
    unit testing the way `_locked_record_ids`/`_screening_state`/
    `_ensure_offline_decisions` already are. This test closes that gap by
    calling `_validate_extraction_artifact` directly (no bridge, stub, or
    HTTP server involved) against a hand-written artifact file that is
    otherwise entirely valid against the requested field specification but
    carries one additional, unvalidated top-level field set to a
    non-finite float, and asserts `BridgeError` (not `TypeError`/
    `ValueError`) is raised, citing `record_hash`."""
    source = b"Sample size was 240 participants.\n"
    path = tmp_path / "extraction.json"
    path.write_text(
        json.dumps(
            {
                "version": "extraction-record-v1",
                "record_id": "rec-1",
                "source_hash": hashlib.sha256(source).hexdigest(),
                "provenance_file": "extraction/source/study.txt",
                "fields": [
                    {"field": "sample_size", "status": "not_reported", "value": "", "span": None}
                ],
                "extra_engine_metadata": float("nan"),
                "record_hash": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(BridgeError, match="record_hash"):
        _validate_extraction_artifact(
            path,
            source_bytes=source,
            record_id="rec-1",
            provenance_file="extraction/source/study.txt",
            requested_fields=[{"field": "sample_size", "status": "not_reported"}],
        )


def test_validate_rob_artifact_fails_closed_on_non_finite_hash_body(tmp_path):
    """The `_validate_rob_artifact` sibling of the gap the test immediately
    above closes for `_validate_extraction_artifact`: its own `rob_hash`
    re-verification (`rob_hash != _object_hash(body)`) shared the identical
    missing `try`/`except (TypeError, ValueError)` guard around
    `_object_hash(body)`. This calls `_validate_rob_artifact` directly (no
    bridge, stub, or HTTP server involved) against a hand-written artifact
    file that is otherwise entirely valid against the requested
    specification but carries one additional, unvalidated top-level field
    set to a non-finite float, and asserts `BridgeError` (not `TypeError`/
    `ValueError`) is raised, citing `rob_hash`."""
    requested_spec = {
        "record_id": "rec-1",
        "tool": "protocol-rob-v1",
        "overall_judgment": "some_concerns",
        "domains": [
            {
                "domain": "randomization",
                "judgment": "low_risk",
                "rationale": "Rationale text.",
                "source_links": ["Methods p.2"],
            }
        ],
    }
    path = tmp_path / "rob.json"
    path.write_text(
        json.dumps(
            {
                "version": "rob-assessment-v1",
                "record_id": "rec-1",
                "tool": "protocol-rob-v1",
                "overall_judgment": "some_concerns",
                "domains": [
                    {
                        "domain": "randomization",
                        "judgment": "low_risk",
                        "rationale": "Rationale text.",
                        "source_links": ["Methods p.2"],
                    }
                ],
                "extra_engine_metadata": float("nan"),
                "rob_hash": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(BridgeError, match="rob_hash"):
        _validate_rob_artifact(path, requested_spec=requested_spec, extraction_record_id="rec-1")


def test_rob_requires_completed_extraction(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "RoB gate"})
        status, payload = _request(
            server, f"/api/runs/{run['id']}/rob", "POST", _rob_body()
        )
        assert status == 409, payload
        assert "extraction" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_rob_preserves_bounded_domains_and_hash(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload
        assert payload["rob"]["domain_count"] == 2, payload
        assert len(payload["rob"]["rob_hash"]) == 64, payload
        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "rob" / "spec.json").is_file()
        artifact = json.loads((run_dir / "rob.json").read_text(encoding="utf-8"))
        assert artifact["domains"][1]["judgment"] == "not_assessed"
        assert artifact["domains"][1]["source_links"] == []
    finally:
        server.shutdown()
        thread.join()


def test_rob_failure_and_false_success_leave_pending_stage(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, rob_returncode=7, rob_stderr="rob failed")
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 400, payload
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["rob"] == "pending"
        assert not (tmp_path / "runs" / run_id / "rob.json").exists()
    finally:
        server.shutdown()
        thread.join()

    _install_stub_bridge(monkeypatch, rob_skip_output_on_success=True)
    server, thread = _server(tmp_path / "false-success")
    try:
        run_id = _extracted_run(server, name="RoB false success")
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 400, payload
        assert "did not produce" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_rob_rejects_malformed_or_mismatched_engine_output(tmp_path, monkeypatch):
    def mutate(body):
        domains = [dict(entry) for entry in body["domains"]]
        domains[0]["domain"] = "invented-domain"
        return {**body, "domains": domains}

    _install_stub_bridge(monkeypatch, rob_output_mutation=mutate)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 400, payload
        assert "domains do not match" in payload["error"]
        assert not (tmp_path / "runs" / run_id / "rob.json").exists()
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("mutation", [
    lambda body: {
        **body,
        "domains": [{**body["domains"][0], "rationale": "engine replacement"}, body["domains"][1]],
    },
    lambda body: {
        **body,
        "domains": [{**body["domains"][0], "source_links": ["engine replacement"]}, body["domains"][1]],
    },
])
def test_rob_rejects_replaced_rationale_or_source_links(tmp_path, monkeypatch, mutation):
    _install_stub_bridge(monkeypatch, rob_output_mutation=mutation)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 400, payload
        assert "rationale or source_links" in payload["error"], payload
        assert not (tmp_path / "runs" / run_id / "rob.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_extraction_retry_invalidates_completed_rob(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 200, payload
        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "rob.json").is_file()
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(b"Sample size was 240 participants."),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "pending"
        assert not (run_dir / "rob.json").exists()
        assert not (run_dir / "rob").exists()
    finally:
        server.shutdown()
        thread.join()


def test_upstream_screen_retry_invalidates_completed_rob(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 200, payload
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "pending"
        assert payload["run"]["stages"]["rob"] == "pending"
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "extraction.json").exists()
        assert not (run_dir / "rob.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_upstream_screen_retry_invalidates_completed_synthesis(tmp_path, monkeypatch):
    """`test_upstream_screen_retry_invalidates_completed_rob` immediately
    above only proves an ordinary `screen` re-run reaches `extraction` and
    `rob` -- it never lays down a `synthesis` artifact first (`_extracted_run`
    stops at extraction), so it cannot tell whether the cascade actually
    reaches `synthesis` too. `_invalidate_package_and_replay` ->
    `_invalidate_rob_assessment` -> `_invalidate_synthesis` already chains
    that far in server.py, and
    `test_rob_retry_invalidates_completed_synthesis` already proves `rob`'s
    own re-run reaches `synthesis`, and
    `test_human_adjudication_toggle_on_invalidates_extraction_rob_and_synthesis_artifacts_and_stages`
    already proves the *toggle* call site reaches `synthesis` -- but no
    existing test proves the plain, ordinary upstream `screen` re-run call
    site (the one `test_upstream_screen_retry_invalidates_completed_rob`
    already exercises for `rob`) reaches one stage further, to `synthesis`,
    when all three of extraction/rob/synthesis already exist on the same
    run. This closes that narrow coverage-symmetry gap; it exercises no new
    server-side code path."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _robbed_run(server)
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 200, payload
        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "synthesis.json").is_file()

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "pending", payload
        assert payload["run"]["stages"]["rob"] == "pending", payload
        assert payload["run"]["stages"]["synthesis"] == "pending", payload
        assert not (run_dir / "extraction.json").exists()
        assert not (run_dir / "rob.json").exists()
        assert not (run_dir / "synthesis.json").exists()
        assert not (run_dir / "synthesis").exists()

        names = {item["name"] for item in payload["artifacts"]}
        assert "synthesis.json" not in names, names
    finally:
        server.shutdown()
        thread.join()


def test_synthesis_requires_completed_rob(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server)
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 409, payload
        assert "rob" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_synthesis_validates_golden_single_study_calculation(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _robbed_run(server)
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload
        assert payload["synthesis"]["status"] == "calculated", payload
        assert payload["synthesis"]["measure"] == "risk_difference", payload
        artifact = json.loads(
            (tmp_path / "runs" / run_id / "synthesis.json").read_text(encoding="utf-8")
        )
        assert artifact["point_estimate"] == 0.1
        assert artifact["uncertainty_flags"] == []
        assert len(artifact["calculation_hash"]) == 64
    finally:
        server.shutdown()
        thread.join()


def test_synthesis_preserves_insufficient_data_uncertainty(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _robbed_run(server)
        body = _synthesis_body(measure="risk_ratio")
        body["counts"]["treated_events"] = 0
        status, payload = _request(server, f"/api/runs/{run_id}/synthesize", "POST", body)
        assert status == 200, payload
        assert payload["synthesis"]["status"] == "insufficient_data", payload
        artifact = json.loads(
            (tmp_path / "runs" / run_id / "synthesis.json").read_text(encoding="utf-8")
        )
        assert artifact["point_estimate"] is None
        assert "zero_event_cell_ratio_measure_undefined" in artifact["uncertainty_flags"]
    finally:
        server.shutdown()
        thread.join()


def test_synthesis_failure_false_success_and_tamper_leave_pending_stage(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, synthesis_returncode=7, synthesis_stderr="synthesis failed")
    server, thread = _server(tmp_path)
    try:
        run_id = _robbed_run(server)
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 400, payload
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["synthesis"] == "pending"
        assert not (tmp_path / "runs" / run_id / "synthesis.json").exists()
    finally:
        server.shutdown()
        thread.join()

    def mutate(body):
        return {**body, "point_estimate": body["point_estimate"] + 0.01}

    _install_stub_bridge(monkeypatch, synthesis_output_mutation=mutate)
    server, thread = _server(tmp_path / "tamper")
    try:
        run_id = _robbed_run(server, name="Synthesis tamper")
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 400, payload
        assert "point_estimate" in payload["error"]
        assert not (tmp_path / "tamper" / "runs" / run_id / "synthesis.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_synthesis_false_success_missing_output_is_rejected(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, synthesis_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        run_id = _robbed_run(server, name="Synthesis missing output")
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 400, payload
        assert "did not produce" in payload["error"]
        record = json.loads((tmp_path / "runs" / run_id / "run.json").read_text(encoding="utf-8"))
        assert record["stages"]["synthesis"] == "pending"
        assert not (tmp_path / "runs" / run_id / "synthesis.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_rob_retry_invalidates_completed_synthesis(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _robbed_run(server)
        status, payload = _request(
            server, f"/api/runs/{run_id}/synthesize", "POST", _synthesis_body()
        )
        assert status == 200, payload
        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "synthesis.json").is_file()
        status, payload = _request(server, f"/api/runs/{run_id}/rob", "POST", _rob_body())
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "pending"
        assert not (run_dir / "synthesis.json").exists()
        assert not (run_dir / "synthesis").exists()
    finally:
        server.shutdown()
        thread.join()


def test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays(tmp_path, monkeypatch):
    """Offline, always-run stub counterpart to phase 10's pinned real-engine
    contract check (`test_full_offline_stack_runs_stages_one_through_nine_and_replays`
    below, skipped when the isolated engine checkout is absent): one run
    crosses every current WebUI stage, preserves one record identity through
    extraction/RoB/synthesis, and still reaches byte-stable package replay.
    This is a local deterministic fixture driven entirely through
    `_install_stub_bridge`; it is not scientific validation or live API
    qualification, and it does not stand in for the real-engine check.

    This test was previously named identically to the real-engine test added
    later in this file (`test_full_offline_stack_runs_stages_one_through_nine_and_replays`).
    Two top-level Python functions sharing one name silently shadow one
    another in the module namespace pytest collects from, so only the
    second (real-engine, skip-guarded) definition was ever actually
    collected and run -- this stub-based fixture was dead code, never
    executed, even though this package's own offline test suite is supposed
    to never depend on the skip-guarded real-engine check for its coverage
    (see `test_stub_package_replay_after_extraction_rob_synthesis_stays_byte_stable`,
    which was added later specifically to close that same coverage gap
    without realizing this earlier, differently-scoped fixture had already
    silently stopped running). Renaming it uniquely restores its collection
    and execution as part of the always-run offline suite."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server, name="Full offline stack")
        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete", payload

        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id="rec-1"),
        )
        assert status == 200, payload
        status, payload = _request(
            server, f"/api/runs/{run_id}/rob", "POST", _rob_body(record_id="rec-1")
        )
        assert status == 200, payload
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id="rec-1"),
        )
        assert status == 200, payload
        status, package = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, package
        status, replay = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, replay
        assert replay["replay"]["byte_stable"] is True, replay

        run_dir = tmp_path / "runs" / run_id
        extraction = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        rob = json.loads((run_dir / "rob.json").read_text(encoding="utf-8"))
        synthesis = json.loads((run_dir / "synthesis.json").read_text(encoding="utf-8"))
        assert extraction["record_id"] == rob["record_id"] == synthesis["record_id"] == "rec-1"
        assert extraction["source_hash"] == hashlib.sha256(source).hexdigest()
        assert package["run"]["stages"]["package"] == "complete"
        assert replay["run"]["stages"]["replay"] == "complete"
        assert {
            name: (run_dir / name).is_file()
            for name in ("frozen.json", "dedup.json", "lock.json", "screen.json", "extraction.json", "rob.json", "synthesis.json")
        } == {
            name: True
            for name in ("frozen.json", "dedup.json", "lock.json", "screen.json", "extraction.json", "rob.json", "synthesis.json")
        }
    finally:
        server.shutdown()
        thread.join()


def test_stub_full_offline_stack_with_human_adjudication_toggle_runs_stages_one_through_nine_and_replays(
    tmp_path, monkeypatch
):
    """docs/UI_BUILD_LOOP.md phase 10 ("Full offline stack") status
    describes the always-run stub counterpart to the pinned real-engine
    contract check as covering "run creation, doctor, import, dedup, lock,
    the human_adjudication toggle, screen/adjudicate, source-anchored
    extract, bounded rob, single-study synthesize, package, and replay".
    `test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays`
    above exercises every one of those actions except the
    human_adjudication toggle and `adjudicate` themselves: it drives
    screening through `_screened_run`, whose stub screen decisions are all
    unambiguous ("include"/"exclude") and whose human_adjudication setting
    stays at its "off" default throughout, so that test's "screen" stage
    never actually reads "blocked" and `adjudicate` is never called. That
    leaves the toggle/blocking-queue portion of phase 10's own stated scope
    uncovered by this package's always-run offline suite -- only the
    skip-guarded real-engine test below exercises it, and only when a
    cached engine screening result happens to abstain.

    This test closes that specific gap: it drives one continuous run
    through the deterministic stub bridge with a screening result that
    deliberately abstains, turns `human_adjudication` on *before*
    screening (so the abstention genuinely blocks the run's `screen`
    stage), resolves it through `adjudicate`, and then continues through
    extraction, RoB, synthesis, package, and replay from that
    human-resolved record -- proving the toggle/adjudicate path and the
    rest of phase 10's scope hold together in one run, unconditionally,
    without depending on whether the real engine checkout happens to be
    present or happens to abstain."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        status, run = _request(
            server, "/api/runs", "POST", {"name": "Full offline stack (toggle)"}
        )
        assert status == 201, run
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {
                "filename": "toggle.ris",
                "content_base64": _b64(
                    b"TY  - JOUR\nTI  - Toggle fixture\nAU  - Doe, Jane\nPY  - 2024\nER  -\n"
                ),
            },
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["screen"]["blocked"] is True, payload
        assert payload["screen"]["remaining"] == ["rec-1"], payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id="rec-1"),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/rob", "POST", _rob_body(record_id="rec-1")
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id="rec-1"),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload

        status, package = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, package
        assert package["run"]["stages"]["package"] == "complete", package
        manifest = package["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), package

        status, replay = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, replay
        assert replay["run"]["stages"]["replay"] == "complete", replay
        assert replay["replay"]["byte_stable"] is True, replay

        run_dir = tmp_path / "runs" / run_id
        extraction = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        rob = json.loads((run_dir / "rob.json").read_text(encoding="utf-8"))
        synthesis = json.loads((run_dir / "synthesis.json").read_text(encoding="utf-8"))
        assert extraction["record_id"] == rob["record_id"] == synthesis["record_id"] == "rec-1"
        assert extraction["source_hash"] == hashlib.sha256(source).hexdigest()

        status, record = _request(server, f"/api/runs/{run_id}")
        assert status == 200, record
        for stage in (
            "initialized",
            "doctor",
            "import",
            "dedup",
            "lock",
            "screen",
            "extraction",
            "rob",
            "synthesis",
            "package",
            "replay",
        ):
            assert record["stages"][stage] == "complete", (stage, record)
    finally:
        server.shutdown()
        thread.join()


def test_import_preserves_raw_source_bytes_and_produces_frozen_artifact(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 2 requires "source preservation and
    engine import output" to both be verified. The uploaded RIS source is
    deliberately given unusual whitespace and CRLF line endings to prove
    the on-disk artifact is byte-for-byte identical to the upload, not
    normalized by the server."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Import"})
        run_id = run["id"]

        raw = b"TY  - JOUR\r\nTI  - A title with  double spaces \nAU  - Doe, Jane\nER  -  \n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(raw)},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload
        assert payload["import"]["format"] == "ris", payload
        assert payload["import"]["source"] == "source/records.ris", payload

        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "source" / "records.ris").read_bytes()
        assert on_disk == raw

        names = {item["name"] for item in payload["artifacts"]}
        assert "source/records.ris" in names
        assert "frozen.json" in names

        _, listing = _request(server, f"/api/runs/{run_id}/artifacts")
        assert {item["name"] for item in listing["artifacts"]} == names
    finally:
        server.shutdown()
        thread.join()


def test_import_preserves_non_utf8_bytes_and_bom(tmp_path, monkeypatch):
    """The prior upload path decoded the browser upload with `file.text()`
    (implicit UTF-8, replacing invalid sequences) and re-encoded it with
    `.encode("utf-8")` on the server -- a round trip that only happens to be
    lossless for a Python `str` that was already valid Unicode, and cannot
    verify the actual browser upload contract. Real RIS/NBIB exports are
    not guaranteed to be UTF-8 (Windows-1252/Latin-1 exports with
    non-ASCII author names are common) and may carry a byte-order mark.
    This drives the wire contract with genuinely non-UTF-8 bytes -- a
    Windows-1252 byte (0x92, "right single quotation mark", which is not
    valid UTF-8 on its own) and a UTF-8 BOM -- and asserts the persisted
    artifact is byte-for-byte identical, not silently altered or replaced
    with U+FFFD."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Non-UTF-8 import"})
        run_id = run["id"]

        raw = (
            b"\xef\xbb\xbf"  # UTF-8 byte-order mark
            b"TY  - JOUR\r\n"
            b"AU  - Doe, Jos\x92 (Windows-1252 apostrophe, invalid as UTF-8)\r\n"
            b"ER  -  \r\n"
        )
        assert b"\xef\xbb\xbf" in raw
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "nonutf8.ris", "content_base64": _b64(raw)},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload

        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "source" / "nonutf8.ris").read_bytes()
        assert on_disk == raw
    finally:
        server.shutdown()
        thread.join()


def test_import_rejects_invalid_base64_content(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Bad base64"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": "not valid base64!!"},
        )
        assert status == 400, payload
        assert "base64" in payload["error"], payload

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "source").exists()
    finally:
        server.shutdown()
        thread.join()


def test_import_accepts_nbib_extension(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "NBIB import"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.nbib", "content_base64": _b64(b"PMID- 1\nTI  - Title\n")},
        )
        assert status == 200, payload
        assert payload["import"]["format"] == "nbib", payload
        assert payload["import"]["source"] == "source/records.nbib", payload
    finally:
        server.shutdown()
        thread.join()


def test_import_rejects_unsupported_extension(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Bad extension"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.txt", "content_base64": _b64(b"not ris or nbib")},
        )
        assert status == 400, payload
        assert "RIS" in payload["error"] and "NBIB" in payload["error"], payload

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "source" / "records.txt").exists()
        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["import"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def test_import_rejects_empty_content(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Empty upload"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": ""},
        )
        assert status == 400, payload
    finally:
        server.shutdown()
        thread.join()


def test_import_rejects_path_traversal_filename(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Traversal"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "../../etc/passwd.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 400, payload

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "source").exists()
        escaped = tmp_path / "etc"
        assert not escaped.exists()
    finally:
        server.shutdown()
        thread.join()


def test_import_engine_failure_surfaces_as_error_and_does_not_mark_stage_complete(tmp_path, monkeypatch):
    """A raw upload written to disk is source preservation, not a claim
    that the import succeeded. If the engine's `import` command fails
    (nonzero exit), the run's `import` stage must NOT be marked complete
    and the failure must surface to the operator, exactly like a real
    bridge failure for any other action -- while the preserved raw source
    file remains on disk regardless."""
    _install_stub_bridge(
        monkeypatch,
        import_returncode=1,
        import_stderr="engine: malformed RIS record on line 4\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Import failure"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 400, payload
        assert "malformed RIS record" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["import"] == "pending", record

        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "source" / "records.ris").is_file()
        names = {item["name"] for item in _request(server, f"/api/runs/{run_id}/artifacts")[1]["artifacts"]}
        assert "frozen.json" not in names
    finally:
        server.shutdown()
        thread.join()


def test_import_failure_after_partial_engine_output_cleans_up_artifacts(tmp_path, monkeypatch):
    """A `BridgeError` on nonzero exit does not, by itself, guarantee the
    engine never touched disk before failing: a real crash can happen after
    the engine has already written a partial or fully-formed
    `frozen.json`/`raw/` output. Leaving that output in place would leave a
    misleading frozen artifact for an import that never actually succeeded,
    violating the same fail-closed guarantee already enforced for a stale
    artifact left by a *prior successful* import (see
    test_reimport_failure_invalidates_prior_success). The preserved raw
    source upload must still survive the failure."""
    _install_stub_bridge(
        monkeypatch,
        import_returncode=1,
        import_stderr="engine: crashed after writing partial output\n",
        import_write_output_before_failure=True,
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Partial output failure"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 400, payload
        assert "crashed after writing partial output" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["import"] == "pending", record

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "frozen.json").exists()
        assert not (run_dir / "raw").exists()
        assert (run_dir / "source" / "records.ris").is_file()

        names = {item["name"] for item in _request(server, f"/api/runs/{run_id}/artifacts")[1]["artifacts"]}
        assert "frozen.json" not in names
        assert "raw/raw.json" not in names
        assert "source/records.ris" in names
    finally:
        server.shutdown()
        thread.join()


def test_import_zero_exit_without_frozen_artifact_fails_closed(tmp_path, monkeypatch):
    """A zero exit code from the engine's `import` command is necessary but
    not sufficient: it must also have actually produced `frozen.json`. If
    the engine reports success (exit 0) without writing the frozen
    artifact -- for example a CLI argument-shape drift, or a bug that
    no-ops instead of erroring -- the run's `import` stage must NOT be
    marked complete, the failure must surface to the operator, and no
    partial output (there is none here) or stale artifact is left behind.
    The preserved raw source upload must still survive."""
    _install_stub_bridge(monkeypatch, import_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "False success import"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 400, payload
        assert "frozen" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["import"] == "pending", record

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "frozen.json").exists()
        assert not (run_dir / "raw").exists()
        assert (run_dir / "source" / "records.ris").is_file()

        names = {item["name"] for item in _request(server, f"/api/runs/{run_id}/artifacts")[1]["artifacts"]}
        assert "frozen.json" not in names
        assert "source/records.ris" in names
    finally:
        server.shutdown()
        thread.join()


def test_reimport_failure_invalidates_prior_success(tmp_path, monkeypatch):
    """A successful import followed by a *failed* re-import must not leave
    the run falsely representing the failed run as frozen: the stale
    `frozen.json`/`raw/` artifacts from the earlier success must be removed
    before the engine is re-invoked, and the run's `import` stage must not
    still read `complete` afterward."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Reimport"})
        run_id = run["id"]

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload
        names = {item["name"] for item in payload["artifacts"]}
        assert "frozen.json" in names
        assert "raw/raw.json" in names

        _install_stub_bridge(
            monkeypatch,
            import_returncode=1,
            import_stderr="engine: malformed RIS record on line 4\n",
        )
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records2.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 400, payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["import"] == "pending", record

        _, listing = _request(server, f"/api/runs/{run_id}/artifacts")
        remaining = {item["name"] for item in listing["artifacts"]}
        assert "frozen.json" not in remaining, remaining
        assert "raw/raw.json" not in remaining, remaining
        # The originally preserved raw source upload is untouched.
        assert "source/records.ris" in remaining, remaining
    finally:
        server.shutdown()
        thread.join()


def test_dedup_requires_import_stage_complete(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 3 requires "stage ordering" to be
    verified: `dedup` must be refused before this run's `import` stage is
    recorded complete, with the actual stage-gate status code (409), not a
    generic error."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Dedup before import"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 409, payload
        assert "import" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["dedup"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def test_lock_requires_dedup_stage_complete(tmp_path, monkeypatch):
    """Same stage-ordering contract as `test_dedup_requires_import_stage_complete`,
    one stage further: `lock` must be refused before this run's `dedup`
    stage is recorded complete, even once `import` is complete."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Lock before dedup"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 409, payload
        assert "dedup" in payload["error"], payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 409, payload
        assert "dedup" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["lock"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def _import_then_dedup(server, run_id, filename="records.ris", content=b"TY  - JOUR\nER  -\n"):
    status, payload = _request(
        server,
        f"/api/runs/{run_id}/import",
        "POST",
        {"filename": filename, "content_base64": _b64(content)},
    )
    assert status == 200, payload
    status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
    assert status == 200, payload
    return payload


def test_dedup_produces_hashed_artifact_and_advances_stage(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 3 requires "artifact hashes" to be
    verified: a successful `dedup` must advance the run's `dedup` stage to
    `complete`, produce `dedup.json` on disk, and report a sha256 digest
    that actually matches the artifact's on-disk bytes -- both in the
    action response and in the general artifact listing."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Dedup hash"})
        run_id = run["id"]

        payload = _import_then_dedup(server, run_id)
        assert payload["run"]["stages"]["dedup"] == "complete", payload
        assert payload["dedup"]["artifact"] == "dedup.json", payload

        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "dedup.json").read_bytes()
        expected = hashlib.sha256(on_disk).hexdigest()
        assert payload["dedup"]["sha256"] == expected, payload

        names = {item["name"]: item for item in payload["artifacts"]}
        assert "dedup.json" in names
        assert names["dedup.json"]["sha256"] == expected
        assert names["dedup.json"]["bytes"] == len(on_disk)

        _, listing = _request(server, f"/api/runs/{run_id}/artifacts")
        listed = {item["name"]: item for item in listing["artifacts"]}
        assert listed["dedup.json"]["sha256"] == expected
    finally:
        server.shutdown()
        thread.join()


def test_dedup_is_deterministic_for_identical_frozen_input(tmp_path, monkeypatch):
    """"Deterministic deduplication" (docs/UI_BUILD_LOOP.md stage 3) means
    re-running `dedup` against the same frozen input must reproduce the
    same content hash, not merely "succeed again". This drives two
    successive `dedup` calls against a run whose `import` never changes
    and asserts their artifact hashes are byte-identical."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Deterministic dedup"})
        run_id = run["id"]

        first = _import_then_dedup(server, run_id)
        first_hash = first["dedup"]["sha256"]

        status, second = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, second
        assert second["dedup"]["sha256"] == first_hash, second
    finally:
        server.shutdown()
        thread.join()


def test_lock_produces_hashed_artifact_and_advances_stage(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Lock hash"})
        run_id = run["id"]
        _import_then_dedup(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload
        assert payload["lock"]["artifact"] == "lock.json", payload

        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "lock.json").read_bytes()
        expected = hashlib.sha256(on_disk).hexdigest()
        assert payload["lock"]["sha256"] == expected, payload

        names = {item["name"]: item for item in payload["artifacts"]}
        assert names["lock.json"]["sha256"] == expected
    finally:
        server.shutdown()
        thread.join()


def test_reimport_invalidates_dedup_and_lock_artifacts_and_stages(tmp_path, monkeypatch):
    """A run that has completed `import` -> `dedup` -> `lock` and is then
    re-imported (a new/updated source upload) must not keep claiming its
    stale `dedup`/`lock` artifacts and stage completions -- both
    deterministically derive from the frozen import output that just
    changed. This mirrors the existing "re-import invalidates a stale
    frozen artifact" contract one stage further downstream."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Reimport cascade"})
        run_id = run["id"]
        _import_then_dedup(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records2.ris", "content_base64": _b64(b"TY  - JOUR\nAU  - New\nER  -\n")},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload
        assert payload["run"]["stages"]["dedup"] == "pending", payload
        assert payload["run"]["stages"]["lock"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "dedup.json" not in names, names
        assert "lock.json" not in names, names

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "dedup.json").exists()
        assert not (run_dir / "lock.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_redup_invalidates_lock_artifact_and_stage(tmp_path, monkeypatch):
    """Re-running `dedup` after `lock` has already completed must
    invalidate the stale `lock` artifact/stage too, since lock
    deterministically derives from the dedup output that just changed."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Redup cascade"})
        run_id = run["id"]
        _import_then_dedup(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload
        assert payload["run"]["stages"]["lock"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "lock.json" not in names, names
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "lock.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_relock_invalidates_screen_artifact_and_stage(tmp_path, monkeypatch):
    """Re-running `lock` after `screen` has already completed must
    invalidate the stale `screen`/`adjudications` artifacts and the
    `screen` stage completion too, since screen deterministically derives
    from the lock output that just changed -- the same cascade stage 3
    already established for `dedup` invalidating a stale `lock`."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Relock cascade"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload
        assert payload["run"]["stages"]["screen"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "screen.json" not in names, names
        assert "adjudications.json" not in names, names
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "screen.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_redup_invalidates_screen_artifact_and_stage(tmp_path, monkeypatch):
    """Re-running `dedup` after `lock` -> `screen` have already completed
    must invalidate the stale `screen`/`adjudications` artifacts and the
    `screen`/`lock` stage completions, since both deterministically derive
    (transitively) from the dedup output that just changed."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Redup screen cascade"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload
        assert payload["run"]["stages"]["lock"] == "pending", payload
        assert payload["run"]["stages"]["screen"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "screen.json" not in names, names
    finally:
        server.shutdown()
        thread.join()


def test_reimport_invalidates_screen_artifact_and_stage(tmp_path, monkeypatch):
    """Re-running `import` after the full `dedup` -> `lock` -> `screen`
    chain has completed must invalidate every downstream artifact and
    stage, including `screen`."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Reimport screen cascade"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records2.ris", "content_base64": _b64(b"TY  - JOUR\nAU  - New\nER  -\n")},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "pending", payload
        assert payload["run"]["stages"]["lock"] == "pending", payload
        assert payload["run"]["stages"]["screen"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "screen.json" not in names, names
    finally:
        server.shutdown()
        thread.join()


def test_upstream_rerun_discards_screen_inputs_when_record_ids_change(tmp_path, monkeypatch):
    """Upstream reruns must not reuse a decision map or cache produced for
    an earlier locked population: after the stub switches from rec-1 to
    rec-2, import/dedup/lock invalidation removes both screening inputs and
    the next screen run reports only the new record id."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "include"}],
        lock_record_ids=["rec-1"],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Changed screening population"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        run_dir = tmp_path / "runs" / run_id
        (run_dir / "decisions.json").write_text('{"rec-1":"include"}\n', encoding="utf-8")
        (run_dir / "cache").mkdir(exist_ok=True)
        (run_dir / "cache" / "old.json").write_text("old\n", encoding="utf-8")

        _install_stub_bridge(
            monkeypatch,
            screen_decisions=[{"id": "rec-2", "decision": "include"}],
            lock_record_ids=["rec-2"],
        )
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records2.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload
        assert not (run_dir / "decisions.json").exists()
        assert not (run_dir / "cache").exists()

        assert _request(server, f"/api/runs/{run_id}/dedup", "POST", {})[0] == 200
        assert not (run_dir / "decisions.json").exists()
        assert not (run_dir / "cache").exists()
        assert _request(server, f"/api/runs/{run_id}/lock", "POST", {})[0] == 200
        assert not (run_dir / "decisions.json").exists()
        assert not (run_dir / "cache").exists()

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert [row["id"] for row in payload["screen"]["decisions"]] == ["rec-2"], payload
    finally:
        server.shutdown()
        thread.join()


def test_dedup_engine_failure_does_not_mark_stage_complete(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        dedup_returncode=1,
        dedup_stderr="engine: dedup crashed\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Dedup failure"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 400, payload
        assert "dedup crashed" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["dedup"] == "pending", record

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "dedup.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_lock_engine_failure_does_not_mark_stage_complete(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        lock_returncode=1,
        lock_stderr="engine: lock crashed\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Lock failure"})
        run_id = run["id"]
        _import_then_dedup(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 400, payload
        assert "lock crashed" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["lock"] == "pending", record

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "lock.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_dedup_zero_exit_without_artifact_fails_closed(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, dedup_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "False success dedup"})
        run_id = run["id"]
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 400, payload
        assert "dedup" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["dedup"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "dedup.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_lock_zero_exit_without_artifact_fails_closed(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, lock_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "False success lock"})
        run_id = run["id"]
        _import_then_dedup(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 400, payload
        assert "lock" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["lock"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "lock.json").exists()
    finally:
        server.shutdown()
        thread.join()


def _import_dedup_lock(server, run_id, filename="records.ris", content=b"TY  - JOUR\nER  -\n"):
    """Drive a run through `import` -> `dedup` -> `lock` (the full stage-3
    prerequisite chain `screen` requires) and return the `lock` action's
    response payload."""
    _import_then_dedup(server, run_id, filename=filename, content=content)
    status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
    assert status == 200, payload
    return payload


def test_screen_requires_lock_stage_complete(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4 requires "stage ordering" to hold one
    stage further than stage 3: `screen` must be refused before this run's
    `lock` stage is recorded complete, with the actual stage-gate status
    code (409)."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Screen before lock"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 409, payload
        assert "lock" in payload["error"], payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
        )
        assert status == 200, payload
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 409, payload
        assert "lock" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def test_screen_produces_hashed_artifact_and_completes_without_abstentions(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4 requires "artifact hashes" (mirroring
    stage 3) and, with no abstained decisions in the engine's cached
    output, a successful `screen` must advance the run's `screen` stage
    straight to `complete`."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Screen hash"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
        assert payload["screen"]["artifact"] == "screen.json", payload
        assert payload["screen"]["blocked"] is False, payload
        assert payload["screen"]["abstained_ids"] == [], payload

        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "screen.json").read_bytes()
        expected = hashlib.sha256(on_disk).hexdigest()
        assert payload["screen"]["sha256"] == expected, payload

        names = {item["name"]: item for item in payload["artifacts"]}
        assert names["screen.json"]["sha256"] == expected
    finally:
        server.shutdown()
        thread.join()


def test_screen_with_abstentions_completes_by_default_when_adjudication_off(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4 requires "human adjudication off by
    default" to actually mean something: an abstained decision must never
    block the `screen` stage unless adjudication has been explicitly
    turned on. The abstained id is still reported (so an operator can see
    it), just not blocking."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}, {"id": "rec-2", "decision": "include"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Abstain default off"})
        run_id = run["id"]
        assert run["human_adjudication"] == "off"
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
        assert payload["screen"]["blocked"] is False, payload
        assert payload["screen"]["abstained_ids"] == ["rec-1"], payload
    finally:
        server.shutdown()
        thread.join()


def test_screen_blocks_stage_when_adjudication_on_and_abstentions_present(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4's "abstention and blocking queue"
    contract: with human adjudication turned on, an abstained decision
    must block the `screen` stage at `blocked`, not `complete`."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}, {"id": "rec-2", "decision": "include"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Abstain blocks"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["screen"]["blocked"] is True, payload
        assert payload["screen"]["remaining"] == ["rec-1"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] == "blocked", record

        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        assert screening["blocked"] is True, screening
        assert screening["remaining"] == ["rec-1"], screening
        assert screening["abstained_ids"] == ["rec-1"], screening
    finally:
        server.shutdown()
        thread.join()


def test_adjudicate_resolves_queue_and_completes_stage(tmp_path, monkeypatch):
    """Resolving the only remaining blocking-queue entry must flip the
    `screen` stage from `blocked` to `complete`."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Adjudicate resolves"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
        assert payload["adjudicate"]["remaining"] == [], payload
        assert payload["adjudicate"]["blocked"] is False, payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "adjudications.json" in names, names
    finally:
        server.shutdown()
        thread.join()


def test_adjudicate_partial_queue_keeps_stage_blocked(tmp_path, monkeypatch):
    """Resolving only some blocking-queue entries must leave the `screen`
    stage `blocked` until every remaining abstained id has been
    adjudicated."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[
            {"id": "rec-1", "decision": "abstain"},
            {"id": "rec-2", "decision": "abstain"},
        ],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Partial adjudication"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert sorted(payload["screen"]["remaining"]) == ["rec-1", "rec-2"], payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "exclude"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["adjudicate"]["remaining"] == ["rec-2"], payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-2", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
        assert payload["adjudicate"]["remaining"] == [], payload
    finally:
        server.shutdown()
        thread.join()


def test_adjudicate_requires_screen_stage_blocked(tmp_path, monkeypatch):
    """`adjudicate` must be refused (409) both before `screen` has ever run
    and once `screen` already reads `complete` (nothing is blocking)."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Adjudicate gate"})
        run_id = run["id"]

        status, payload = _request(
            server, f"/api/runs/{run_id}/adjudicate", "POST", {"id": "rec-1", "decision": "include"}
        )
        assert status == 409, payload
        assert "blocked" in payload["error"], payload

        _import_dedup_lock(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/adjudicate", "POST", {"id": "rec-1", "decision": "include"}
        )
        assert status == 409, payload
        assert "blocked" in payload["error"], payload
    finally:
        server.shutdown()
        thread.join()


def test_adjudicate_rejects_id_not_in_queue(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Unknown queue id"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        _request(server, f"/api/runs/{run_id}/screen", "POST", {})

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-does-not-exist", "decision": "include"},
        )
        assert status == 400, payload
        assert "not part of" in payload["error"], payload
    finally:
        server.shutdown()
        thread.join()


def test_adjudicate_rejects_invalid_decision_value(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Bad decision"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        _request(server, f"/api/runs/{run_id}/screen", "POST", {})

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "abstain"},
        )
        assert status == 400, payload
        assert "decision" in payload["error"], payload
    finally:
        server.shutdown()
        thread.join()


def test_screening_route_reports_empty_state_before_any_screen_has_run(tmp_path, monkeypatch):
    """The GET `/api/runs/<id>/screening` route (docs/UI_BUILD_LOOP.md
    stage 4, "expose ... an explicit on/off toggle" and "abstention and
    blocking queue") must return a safe, well-shaped all-empty state for a
    run that has never been screened -- not only for a run whose `screen`
    stage already reads `blocked` or `complete`, both of which every other
    test above always drives through first. Without this, an operator UI
    could not render this run's blocking-queue state before `screen` has
    ever been invoked without special-casing "not screened yet" as an
    error, even though `_screening_state` (server.py) is documented to
    treat a missing `screen.json` as an empty-but-valid state rather than a
    failure."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Screening before screen"})
        run_id = run["id"]

        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        assert screening["decisions"] == [], screening
        assert screening["abstained_ids"] == [], screening
        assert screening["adjudications"] == {}, screening
        assert screening["remaining"] == [], screening
        assert screening["blocked"] is False, screening
        assert screening["human_adjudication"] == "off", screening
    finally:
        server.shutdown()
        thread.join()


def test_adjudicate_updates_decision_for_already_resolved_id_while_still_blocked(tmp_path, monkeypatch):
    """Calling `adjudicate` again for an id this run has already resolved
    -- while the `screen` stage is still `blocked` on a *different*
    remaining id -- must overwrite the previously recorded decision rather
    than reject it or silently keep the first one. `adjudicate`'s own gate
    (server.py) only requires the id to currently be part of this run's
    abstained set, not that it have no prior recorded decision yet, so an
    operator correcting an earlier adjudication before the rest of the
    queue is resolved is a legitimate use of that gate, not a corrupted
    state -- distinct from `test_adjudicate_partial_queue_keeps_stage_blocked`
    above, which only ever adjudicates each id once."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[
            {"id": "rec-1", "decision": "abstain"},
            {"id": "rec-2", "decision": "abstain"},
        ],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Revise adjudication"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert sorted(payload["screen"]["remaining"]) == ["rec-1", "rec-2"], payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "exclude"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["adjudicate"]["remaining"] == ["rec-2"], payload

        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        assert screening["adjudications"]["rec-1"] == "exclude", screening

        # Revise the earlier decision before the rest of the queue is
        # resolved; the stage must remain blocked on "rec-2" alone.
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["adjudicate"]["remaining"] == ["rec-2"], payload

        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        assert screening["adjudications"]["rec-1"] == "include", screening

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-2", "decision": "exclude"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        run_dir = tmp_path / "runs" / run_id
        adjudications = json.loads((run_dir / "adjudications.json").read_text(encoding="utf-8"))
        assert adjudications == {"rec-1": "include", "rec-2": "exclude"}, adjudications
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_requires_boolean_enabled_field(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Bad toggle"})
        run_id = run["id"]
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": "yes"}
        )
        assert status == 400, payload
        assert "boolean" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["human_adjudication"] == "off", record
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_toggle_off_unblocks_pending_queue(tmp_path, monkeypatch):
    """Turning adjudication off must discharge an existing block
    immediately, without requiring each queue entry to be individually
    adjudicated."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Toggle off unblocks"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": False}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "off", payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_toggle_on_retroactively_blocks_existing_abstentions(tmp_path, monkeypatch):
    """Turning adjudication on after a run already screened (with
    adjudication off, so it completed unblocked) must retroactively block
    the `screen` stage on any still-unresolved abstention."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Toggle on retroactive"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_toggle_before_any_screen_does_not_touch_screen_stage(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Toggle before screen"})
        run_id = run["id"]
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload
        assert payload["run"]["stages"]["screen"] == "pending", payload
    finally:
        server.shutdown()
        thread.join()


def test_rescreen_invalidates_prior_adjudications(tmp_path, monkeypatch):
    """A fresh `screen` run must wipe any adjudications recorded against
    the previous screening output: the blocking queue's identity is
    entirely a function of the current cached screening result, mirroring
    stage 3's re-run invalidation cascade for `dedup`/`lock`."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Rescreen cascade"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["screen"]["remaining"] == ["rec-1"], payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "adjudications.json" not in names, names
    finally:
        server.shutdown()
        thread.join()


def test_screen_engine_failure_does_not_mark_stage_complete(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        screen_returncode=1,
        screen_stderr="engine: screen crashed\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Screen failure"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 400, payload
        assert "screen crashed" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] == "pending", record

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "screen.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_screen_zero_exit_without_artifact_fails_closed(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, screen_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "False success screen"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 400, payload
        assert "screen" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "screen.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_screen_rejects_duplicate_record_ids(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4's 'one decision per record' contract
    is violated just as much by an engine output that reports the same
    record id twice as by one with an invalid decision value. Shape
    validation alone (every entry is a well-formed `{"id", "decision"}`
    object) is not sufficient: an engine output with a repeated id must
    fail closed instead of letting the run's `screen` stage advance to
    `complete` (or `blocked`) against a decision set that does not
    actually hold exactly one decision per record."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[
            {"id": "rec-1", "decision": "include"},
            {"id": "rec-1", "decision": "exclude"},
        ],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Duplicate record ids"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 400, payload
        assert "rec-1" in payload["error"], payload
        assert "one decision per record" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] != "complete", record
    finally:
        server.shutdown()
        thread.join()


def test_screen_rejects_empty_decisions_array(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4's 'one decision per record' contract
    is violated just as much by an engine output with an empty `decisions`
    array as by a duplicate id: an empty array is still a shape-valid JSON
    array, so shape validation alone would let it mark the run's `screen`
    stage complete even though the engine did not actually screen a single
    locked record. This must fail closed instead."""
    _install_stub_bridge(monkeypatch, screen_decisions=[])
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Empty decisions"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 400, payload
        assert "decisions" in payload["error"], payload
        assert "empty" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] != "complete", record
    finally:
        server.shutdown()
        thread.join()


def test_screen_rejects_decisions_missing_a_locked_record(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 4's "one decision per record" contract
    means every record this run's `lock` stage actually locked, not merely
    whatever ids happen to appear in the screen artifact. A screen output
    that reports decisions for only some of this run's locked records
    (here, `rec-2` is locked but never decided) must fail closed instead of
    marking the run's `screen` stage complete against incomplete
    coverage."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "include"}],
        lock_record_ids=["rec-1", "rec-2"],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Missing locked record"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 400, payload
        assert "rec-2" in payload["error"], payload
        assert "missing" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] != "complete", record
    finally:
        server.shutdown()
        thread.join()


def test_screen_rejects_decision_for_unknown_record_id(tmp_path, monkeypatch):
    """The inverse of `test_screen_rejects_decisions_missing_a_locked_record`:
    a screen artifact containing a decision for a record id that is not
    part of this run's locked record set (an arbitrary/unknown id) must
    also fail closed instead of marking the run's `screen` stage
    complete."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[
            {"id": "rec-1", "decision": "include"},
            {"id": "rec-does-not-exist", "decision": "exclude"},
        ],
        lock_record_ids=["rec-1"],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Unknown locked record"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 400, payload
        assert "rec-does-not-exist" in payload["error"], payload
        assert "not in this run's locked record set" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["screen"] != "complete", record
    finally:
        server.shutdown()
        thread.join()


def test_screening_state_rejects_adjudication_key_not_currently_abstained(tmp_path, monkeypatch):
    """A corrupted (or hand-edited) `adjudications.json` entry whose key
    does not identify a record this run's current `screen.json` actually
    reports as abstained must never be silently accepted as resolving a
    blocking-queue entry: doing so would let the entry incorrectly vanish
    from `remaining` and discharge the run's block."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Corrupt adjudication key"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload

        run_dir = tmp_path / "runs" / run_id
        (run_dir / "adjudications.json").write_text(
            json.dumps({"rec-does-not-exist": "include"}) + "\n", encoding="utf-8"
        )

        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 400, screening
        assert "rec-does-not-exist" in screening["error"], screening
    finally:
        server.shutdown()
        thread.join()


def test_screening_state_rejects_adjudication_value_outside_allowed_set(tmp_path, monkeypatch):
    """A corrupted `adjudications.json` value outside
    `ADJUDICATION_DECISION_VALUES` (for example `"abstain"`, which is
    deliberately excluded -- adjudication resolves an abstention, it does
    not record another one) must never be silently treated as resolving
    the blocking-queue entry it is keyed against."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Corrupt adjudication value"})
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload

        run_dir = tmp_path / "runs" / run_id
        (run_dir / "adjudications.json").write_text(
            json.dumps({"rec-1": "abstain"}) + "\n", encoding="utf-8"
        )

        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 400, screening
        assert "rec-1" in screening["error"], screening
    finally:
        server.shutdown()
        thread.join()


def _import_dedup_lock_screen(server, run_id):
    """Drive a run through the full `import` -> `dedup` -> `lock` ->
    `screen` prerequisite chain `package` requires (default decisions have
    no abstentions, so `screen` always completes unblocked) and return the
    `screen` action's response payload."""
    _import_dedup_lock(server, run_id)
    status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
    assert status == 200, payload
    assert payload["run"]["stages"]["screen"] == "complete", payload
    return payload


def test_package_requires_screen_stage_complete(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 5 requires "stage ordering" to hold one
    stage further than stage 4: `package` must be refused before this run's
    `screen` stage is recorded complete, with the actual stage-gate status
    code (409) -- including while `screen` merely reads `blocked` (screened,
    but still waiting on adjudication), not only while it is `pending`."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Package before screen"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 409, payload
        assert "screen" in payload["error"], payload

        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 409, payload
        assert "screen" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["package"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def test_replay_requires_package_stage_complete(tmp_path, monkeypatch):
    """Same stage-ordering contract one stage further: `replay` must be
    refused before this run's `package` stage is recorded complete, even
    once `screen` is complete."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Replay before package"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 409, payload
        assert "package" in payload["error"], payload

        _import_dedup_lock_screen(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 409, payload
        assert "package" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["replay"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def test_package_produces_hashed_artifact_with_accurate_manifest(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 5 requires "package" to be verified: a
    successful `package` must advance the run's `package` stage to
    `complete`, produce `package.json` on disk, report a sha256 digest that
    actually matches the artifact's on-disk bytes, and its manifest must
    report the true sha256 of every one of this run's
    `PACKAGE_ARTIFACT_NAMES` artifacts."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Package hash"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        assert payload["package"]["artifact"] == "package.json", payload

        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "package.json").read_bytes()
        expected = hashlib.sha256(on_disk).hexdigest()
        assert payload["package"]["sha256"] == expected, payload

        manifest = payload["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), manifest
        for name, digest in manifest.items():
            actual = hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
            assert digest == actual, (name, manifest)

        names = {item["name"]: item for item in payload["artifacts"]}
        assert names["package.json"]["sha256"] == expected
    finally:
        server.shutdown()
        thread.join()


def test_package_requires_all_four_stage_artifacts_present(tmp_path, monkeypatch):
    """A recorded-complete `screen` stage is not itself proof the four
    artifacts a package bundles (`PACKAGE_ARTIFACT_NAMES`) are still on
    disk -- for example if the run directory were tampered with
    out-of-band. `package` must fail closed instead of trusting the stage
    bookkeeping."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Missing package input"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)

        run_dir = tmp_path / "runs" / run_id
        (run_dir / "dedup.json").unlink()

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 400, payload
        assert "dedup.json" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["package"] != "complete", record
    finally:
        server.shutdown()
        thread.join()


def test_package_engine_failure_does_not_mark_stage_complete(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        package_returncode=1,
        package_stderr="engine: package crashed\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Package failure"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 400, payload
        assert "package crashed" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["package"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "package.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_package_zero_exit_without_artifact_fails_closed(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, package_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "False success package"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 400, payload
        assert "package" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["package"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "package.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_package_rejects_manifest_that_does_not_match_on_disk_artifacts(tmp_path, monkeypatch):
    """A well-shaped `package.json` manifest is not, by itself, proof its
    recorded digests are accurate. A manifest entry that does not match the
    real on-disk artifact bytes (for example computed against a stale copy)
    must fail closed instead of being trusted -- the same "necessary but
    not sufficient" posture already enforced for every other artifact this
    package produces."""
    _install_stub_bridge(monkeypatch, package_bad_manifest=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Bad manifest"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 400, payload
        assert "manifest" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["package"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "package.json").exists()
    finally:
        server.shutdown()
        thread.join()


def test_replay_is_byte_stable_by_default_and_matches_package_manifest(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 5's actual "verify byte-stable offline
    replay" contract: a deterministic replay must reproduce every one of
    `PACKAGE_ARTIFACT_NAMES` byte-for-byte, advance the run's `replay`
    stage to `complete` (not merely "ran without error"), and report a
    per-artifact comparison confirming the match."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Byte-stable replay"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload
        assert payload["replay"]["byte_stable"] is True, payload
        assert payload["replay"]["mismatched"] == [], payload
        for name in PACKAGE_ARTIFACT_NAMES:
            assert payload["replay"]["comparison"][name]["match"] is True, payload

        run_dir = tmp_path / "runs" / run_id
        for name in PACKAGE_ARTIFACT_NAMES:
            original = (run_dir / name).read_bytes()
            replayed = (run_dir / "replay" / name).read_bytes()
            assert original == replayed, name

        names = {item["name"] for item in payload["artifacts"]}
        for name in PACKAGE_ARTIFACT_NAMES:
            assert f"replay/{name}" in names, names

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["packaged"] is True, comparison
        assert comparison["replayed"] is True, comparison
        assert comparison["byte_stable"] is True, comparison
    finally:
        server.shutdown()
        thread.join()


def test_replay_detects_byte_mismatch_and_does_not_mark_stage_complete(tmp_path, monkeypatch):
    """The other half of "verify byte-stable offline replay": a replay that
    executes successfully but reproduces different bytes than the package
    manifest recorded must never be reported as `complete` -- it reads
    `mismatched` instead, with the audit comparison (and the non-matching
    replay output itself) preserved for the operator to inspect, not
    silently discarded."""
    _install_stub_bridge(
        monkeypatch,
        replay_mutations={"screen.json": b'{"stub": "replay produced different bytes"}\n'},
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Mismatched replay"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "mismatched", payload
        assert payload["replay"]["byte_stable"] is False, payload
        assert payload["replay"]["mismatched"] == ["screen.json"], payload
        assert payload["replay"]["comparison"]["screen.json"]["match"] is False, payload
        assert payload["replay"]["comparison"]["frozen.json"]["match"] is True, payload

        run_dir = tmp_path / "runs" / run_id
        # The non-matching replay output is preserved as audit evidence, not
        # deleted.
        assert (run_dir / "replay" / "screen.json").is_file()

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["byte_stable"] is False, comparison
        assert comparison["mismatched"] == ["screen.json"], comparison
    finally:
        server.shutdown()
        thread.join()


def test_replay_engine_failure_does_not_mark_stage_complete(tmp_path, monkeypatch):
    _install_stub_bridge(
        monkeypatch,
        replay_returncode=1,
        replay_stderr="engine: replay crashed\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Replay failure"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        _request(server, f"/api/runs/{run_id}/package", "POST", {})

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 400, payload
        assert "replay crashed" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["replay"] == "pending", record
        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "replay").exists()
    finally:
        server.shutdown()
        thread.join()


def test_replay_zero_exit_without_all_artifacts_fails_closed(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, replay_skip_output_on_success=True)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "False success replay"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        _request(server, f"/api/runs/{run_id}/package", "POST", {})

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 400, payload
        assert "replay" in payload["error"].lower(), payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["replay"] == "pending", record
    finally:
        server.shutdown()
        thread.join()


def test_replay_state_before_packaging_is_empty(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Replay state before package"})
        run_id = run["id"]

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["packaged"] is False, comparison
        assert comparison["replayed"] is False, comparison
        assert comparison["byte_stable"] is None, comparison
    finally:
        server.shutdown()
        thread.join()


def test_repackage_after_replay_invalidates_replay_artifact_and_stage(tmp_path, monkeypatch):
    """Re-running `package` after `replay` has already completed must
    invalidate the stale `replay/` artifacts and the `replay` stage
    completion too, since replay deterministically reproduces the package
    output that just changed -- the same cascade posture stage 3/4 already
    established for `dedup`/`lock`/`screen`."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Repackage cascade"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        _request(server, f"/api/runs/{run_id}/package", "POST", {})
        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        assert payload["run"]["stages"]["replay"] == "pending", payload

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "replay").exists()
        names = {item["name"] for item in payload["artifacts"]}
        assert not any(name.startswith("replay/") for name in names), names
    finally:
        server.shutdown()
        thread.join()


def test_rescreen_invalidates_package_and_replay_artifacts_and_stages(tmp_path, monkeypatch):
    """Re-running an earlier stage (`screen`) after the full `package` ->
    `replay` chain has completed must invalidate both downstream
    artifacts/stages, mirroring the existing cascade that already
    invalidates `dedup`/`lock`/`screen` when `import` is re-run."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Rescreen package cascade"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        _request(server, f"/api/runs/{run_id}/package", "POST", {})
        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
        assert payload["run"]["stages"]["package"] == "pending", payload
        assert payload["run"]["stages"]["replay"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "package.json" not in names, names
        assert not any(name.startswith("replay/") for name in names), names

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "package.json").exists()
        assert not (run_dir / "replay").exists()
    finally:
        server.shutdown()
        thread.join()


def test_reimport_invalidates_package_and_replay_artifacts_and_stages(tmp_path, monkeypatch):
    """Same cascade one stage further upstream: re-running `import` after
    the full `dedup` -> `lock` -> `screen` -> `package` -> `replay` chain
    has completed must invalidate every downstream artifact and stage,
    including `package` and `replay`."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Reimport package cascade"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        _request(server, f"/api/runs/{run_id}/package", "POST", {})
        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records2.ris", "content_base64": _b64(b"TY  - JOUR\nAU  - New\nER  -\n")},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "pending", payload
        assert payload["run"]["stages"]["lock"] == "pending", payload
        assert payload["run"]["stages"]["screen"] == "pending", payload
        assert payload["run"]["stages"]["package"] == "pending", payload
        assert payload["run"]["stages"]["replay"] == "pending", payload
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_toggle_on_invalidates_package_and_replay_artifacts_and_stages(
    tmp_path, monkeypatch
):
    """Regression test for the stage-5 review finding that toggling
    `human_adjudication` on can retroactively demote a previously-
    `complete` `screen` stage to `blocked` against an existing unresolved
    abstention -- without ever touching `screen.json` itself -- while
    leaving any `package`/`replay` artifacts and stage completions already
    built on top of that now-stale "screen complete" state untouched. That
    would let a run keep reporting a `complete` package and a byte-stable
    replay while its prerequisite screening stage now reads `blocked`. This
    mirrors the existing cascade
    `test_rescreen_invalidates_package_and_replay_artifacts_and_stages`
    already exercises for an actual `screen` re-run, but triggers the same
    invalidation purely through the `human_adjudication` toggle."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(
            server, "/api/runs", "POST", {"name": "Toggle on package cascade"}
        )
        run_id = run["id"]
        # Screening completes unblocked (adjudication is off by default
        # here), so `package`/`replay` can legitimately build on top of a
        # "complete" screen stage.
        _import_dedup_lock_screen(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        # Turning adjudication on now demotes the run's already-"complete"
        # screen stage to "blocked" against the still-unresolved
        # abstention, purely via the toggle -- `package`/`replay` must not
        # still claim completeness against it.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["run"]["stages"]["package"] == "pending", payload
        assert payload["run"]["stages"]["replay"] == "pending", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "package.json" not in names, names
        assert not any(name.startswith("replay/") for name in names), names

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "package.json").exists()
        assert not (run_dir / "replay").exists()

        # The demotion actually stuck (not merely a transient response
        # field): `package` is refused again until `screen` is resolved.
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 409, payload
        assert "screen" in payload["error"], payload
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_toggle_on_invalidates_extraction_rob_and_synthesis_artifacts_and_stages(
    tmp_path, monkeypatch
):
    """docs/UI_BUILD_LOOP.md phase 10 ("Full offline stack") requires that
    the re-run invalidation cascade already proven for `package`/`replay`
    (docs/UI_BUILD_LOOP.md stage 5) keeps holding once source-anchored
    extraction (stage 7), bounded risk-of-bias (stage 8), and single-study
    synthesis (stage 9) have also been layered on top of a screened run.
    `_invalidate_package_and_replay` (server.py) already removes a run's
    `extraction.json`/`rob.json`/`synthesis.json` artifacts and demotes
    their stage completions every time it runs -- not only
    `package.json`/`replay/` -- and an ordinary `screen` re-run already
    exercises that (see `test_upstream_screen_retry_invalidates_completed_rob`).
    But `human_adjudication`'s retroactive "complete" -> "blocked" screen
    demotion is a *second*, independent call site for that same cascade
    (see server.py's `human_adjudication` branch), and every existing test
    for that specific demotion path
    (`test_human_adjudication_toggle_on_invalidates_package_and_replay_artifacts_and_stages`)
    stops at `package`/`replay` and never lays down extraction/RoB/synthesis
    artifacts first -- so no existing test actually proves the toggle path
    reaches them too, only that an explicit `screen` re-run does. This test
    closes that gap: it drives a run through extraction, RoB, synthesis,
    package, and replay on top of a screening result that leaves one record
    unresolved-abstained (so `screen` still legitimately reads "complete"
    while adjudication is off), then flips `human_adjudication` on and
    asserts that same demotion invalidates extraction/RoB/synthesis, not
    merely package/replay."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[
            {"id": "rec-1", "decision": "include"},
            {"id": "rec-2", "decision": "abstain"},
        ],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(
            server, "/api/runs", "POST", {"name": "Toggle on extraction cascade"}
        )
        run_id = run["id"]
        # Screening completes unblocked (adjudication is off by default
        # here, so the abstained "rec-2" does not block this run yet), so
        # extraction/RoB/synthesis/package/replay can legitimately build on
        # top of a "complete" screen stage.
        _import_dedup_lock_screen(server, run_id)

        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id="rec-1"),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete", payload

        status, payload = _request(
            server, f"/api/runs/{run_id}/rob", "POST", _rob_body(record_id="rec-1")
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id="rec-1"),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        # Turning adjudication on now demotes the run's already-"complete"
        # screen stage to "blocked" against the still-unresolved abstention
        # on "rec-2" -- extraction/RoB/synthesis/package/replay, all built on
        # top of that now-stale "screen complete" state, must not still
        # claim completeness against it.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload
        assert payload["run"]["stages"]["extraction"] == "pending", payload
        assert payload["run"]["stages"]["rob"] == "pending", payload
        assert payload["run"]["stages"]["synthesis"] == "pending", payload
        assert payload["run"]["stages"]["package"] == "pending", payload
        assert payload["run"]["stages"]["replay"] == "pending", payload

        run_dir = tmp_path / "runs" / run_id
        assert not (run_dir / "extraction.json").exists()
        assert not (run_dir / "extraction").exists()
        assert not (run_dir / "rob.json").exists()
        assert not (run_dir / "rob").exists()
        assert not (run_dir / "synthesis.json").exists()
        assert not (run_dir / "synthesis").exists()
        assert not (run_dir / "package.json").exists()
        assert not (run_dir / "replay").exists()

        names = {item["name"] for item in payload["artifacts"]}
        assert "extraction.json" not in names, names
        assert "rob.json" not in names, names
        assert "synthesis.json" not in names, names
        assert "package.json" not in names, names
        assert not any(name.startswith("replay/") for name in names), names

        # The demotion actually stuck (not merely a transient response
        # field): extraction is refused again until screen is resolved.
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id="rec-1"),
        )
        assert status == 409, payload
        assert "screen" in payload["error"], payload
    finally:
        server.shutdown()
        thread.join()


def test_human_adjudication_toggle_off_does_not_invalidate_package_and_replay(
    tmp_path, monkeypatch
):
    """The inverse toggle direction (`off`, discharging a block) never
    demotes a "complete" screen stage -- it can only promote a `blocked`
    stage to `complete` -- so it must not trigger the package/replay
    invalidation cascade at all. This guards against an overly broad fix
    that invalidates on every human_adjudication call regardless of
    direction."""
    _install_stub_bridge(
        monkeypatch,
        screen_decisions=[{"id": "rec-1", "decision": "abstain"}],
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(
            server, "/api/runs", "POST", {"name": "Toggle off no cascade"}
        )
        run_id = run["id"]
        _import_dedup_lock(server, run_id)
        _request(server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True})
        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "blocked", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/adjudicate",
            "POST",
            {"id": "rec-1", "decision": "include"},
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        # Flip adjudication off and back on with nothing left in the
        # blocking queue (already resolved above) -- `screen` stays
        # `complete` throughout, so `package`/`replay` must be untouched.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": False}
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["screen"] == "complete", payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        assert payload["run"]["stages"]["replay"] == "complete", payload

        names = {item["name"] for item in payload["artifacts"]}
        assert "package.json" in names, names
    finally:
        server.shutdown()
        thread.join()


def test_replay_requires_package_manifest_to_match_current_on_disk_artifacts(tmp_path, monkeypatch):
    """A recorded-complete `package` stage is not itself proof its manifest
    still matches this run's current on-disk artifacts -- for example if
    the run directory were tampered with out-of-band (or an earlier stage
    were somehow re-run without going through this handler's own
    invalidation cascade). `replay` must fail closed instead of trusting
    the stage bookkeeping."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Stale package manifest"})
        run_id = run["id"]
        _import_dedup_lock_screen(server, run_id)
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload

        run_dir = tmp_path / "runs" / run_id
        (run_dir / "screen.json").write_bytes(b'{"stub": "tampered after packaging"}\n')

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 400, payload
        assert "screen.json" in payload["error"], payload

        _, record = _request(server, f"/api/runs/{run_id}")
        assert record["stages"]["replay"] != "complete", record
    finally:
        server.shutdown()
        thread.join()


def test_stub_replay_reconstructs_solely_from_package_content(tmp_path, monkeypatch):
    """Regression test for the stage-5 review finding that the deterministic
    stub bridge's `package`/`replay` simulation only proved the WebUI's own
    comparison logic, not that a package artifact is actually sufficient on
    its own to reproduce a run's stage artifacts: the stub's `package`
    command used to write only a sha256 manifest (no artifact content), and
    its `replay` command reconstructed output by copying the *original*
    files still sitting in the run directory next to the package
    (`run_dir = package_path.parent`), never actually reading
    `package.json` itself. Every existing `.../replay` test always ran with
    those originals still present (the WebUI's HTTP `replay` action
    requires exactly that -- see
    `test_replay_requires_package_manifest_to_match_current_on_disk_artifacts`,
    an intentional anti-tampering freshness re-check on this run's *current*
    on-disk artifacts, not a claim about what the package itself must
    contain), so no existing test could tell a genuinely portable package
    apart from a hash-only sidecar that happens to sit beside files that
    never moved.

    This test exercises `EngineBridge.package_run`/`EngineBridge.replay_run`
    directly (the same layer `test_bridge_package_run_invokes_expected_cli_shape`
    and friends exercise), deliberately bypassing `server.py`'s own separate
    `stale_inputs` re-check so it can delete the run's original
    `PACKAGE_ARTIFACT_NAMES` artifacts and move `package.json` itself to an
    unrelated directory *before* calling `replay_run` -- proving the
    package's own embedded content, not any file left on disk next to it,
    is what `replay` reproduces."""
    _install_stub_bridge(monkeypatch)
    bridge = EngineBridge(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    originals = {
        "frozen.json": b'{"stub": "frozen"}\n',
        "dedup.json": b'{"stub": "dedup"}\n',
        "lock.json": b'{"stub": "lock"}\n',
        "screen.json": b'{"stub": "screen"}\n',
    }
    assert set(originals) == set(PACKAGE_ARTIFACT_NAMES)
    for name, content in originals.items():
        (run_dir / name).write_bytes(content)

    package_path = run_dir / "package.json"
    bridge.package_run(run_dir / "screen.json", output_path=package_path)
    package_payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert "artifacts_base64" in package_payload, package_payload
    for name in PACKAGE_ARTIFACT_NAMES:
        assert base64.b64decode(package_payload["artifacts_base64"][name]) == originals[name], name

    # Isolate the package from every original artifact: move `package.json`
    # to an unrelated directory and delete the run's original artifacts
    # entirely, so nothing on disk still holds their bytes except the
    # relocated `package.json` itself.
    isolated_package_path = tmp_path / "isolated" / "package.json"
    isolated_package_path.parent.mkdir()
    isolated_package_path.write_bytes(package_path.read_bytes())
    package_path.unlink()
    for name in PACKAGE_ARTIFACT_NAMES:
        (run_dir / name).unlink()
        assert not (run_dir / name).exists()

    output_dir = tmp_path / "replay-output"
    bridge.replay_run(
        isolated_package_path,
        lock_path=tmp_path / "lock.json",
        dedup_path=tmp_path / "dedup.json",
        cache_dir=tmp_path / "cache",
        output_dir=output_dir,
    )

    for name in PACKAGE_ARTIFACT_NAMES:
        replayed = (output_dir / name).read_bytes()
        assert replayed == originals[name], name
        assert hashlib.sha256(replayed).hexdigest() == package_payload["manifest"][name], name


def test_artifact_inspection_returns_exact_bytes(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 5's "artifact inspection" contract: an
    operator must be able to fetch a specific artifact's exact content
    (not merely its name/size/hash), byte for byte, including for a nested
    artifact path like the preserved raw source upload."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Artifact inspection"})
        run_id = run["id"]
        raw = b"TY  - JOUR\r\nAU  - Doe, Jane\r\nER  -  \r\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {"filename": "records.ris", "content_base64": _b64(raw)},
        )
        assert status == 200, payload

        status, content = _request(server, f"/api/runs/{run_id}/artifacts/source/records.ris")
        assert status == 200, content
        assert content["name"] == "source/records.ris", content
        assert content["bytes"] == len(raw), content
        assert content["sha256"] == hashlib.sha256(raw).hexdigest(), content
        assert base64.b64decode(content["content_base64"]) == raw, content

        status, content = _request(server, f"/api/runs/{run_id}/artifacts/frozen.json")
        assert status == 200, content
        run_dir = tmp_path / "runs" / run_id
        on_disk = (run_dir / "frozen.json").read_bytes()
        assert base64.b64decode(content["content_base64"]) == on_disk, content
    finally:
        server.shutdown()
        thread.join()


def test_artifact_inspection_rejects_path_traversal_and_missing_artifacts(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Artifact inspection traversal"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/artifacts/does-not-exist.json")
        assert status == 400, payload
        assert "not found" in payload["error"], payload

        status, payload = _request(server, f"/api/runs/{run_id}/artifacts/run.json")
        assert status == 400, payload
        assert "not found" in payload["error"], payload

        status, payload = _request(server, f"/api/runs/{run_id}/artifacts/..%2f..%2fetc%2fpasswd")
        assert status == 400, payload
    finally:
        server.shutdown()
        thread.join()


def test_concurrent_imports_on_the_same_run_are_serialized(tmp_path, monkeypatch):
    """Regression test for the reviewed race: `ThreadingHTTPServer`
    dispatches every request on its own thread, so two `import` requests
    against the *same* run id could previously execute their critical
    sections (write the raw source, remove any stale frozen artifact from
    a prior import, invoke the engine, update the run's stage) at the same
    time. That interleaving could let one request's stale-artifact cleanup
    remove a concurrent *successful* request's just-written
    `frozen.json`/`raw/` output while the successful request's own
    response still reported the run's `import` stage as `complete` -- a
    false-complete run with no frozen artifact behind it.

    This drives one import destined to succeed and one destined to fail
    concurrently against the same run. The stub engine holds its
    "critical section" open (via `time.sleep`) long enough to give an
    unserialized implementation a real window to interleave, and records
    whether a second invocation ever started while a first one was still
    in progress. It asserts: (1) the two requests' critical sections never
    actually overlap, and (2) whichever one applied last, the run's final
    persisted state is internally consistent -- the `import` stage reads
    `complete` if and only if `frozen.json` and `raw/raw.json` are
    actually present on disk."""
    in_progress = threading.Event()
    overlap_detected = threading.Event()
    guard = threading.Lock()

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        with guard:
            if in_progress.is_set():
                overlap_detected.set()
            in_progress.set()
        try:
            if args[0] == "init":
                run_dir = Path(args[1])
                for name in ("protocol.json", "procedure.json", "schema.json"):
                    (run_dir / name).write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            # Hold the "critical section" open long enough that an
            # unserialized implementation would have a real chance to run
            # the other request's cleanup/write logic concurrently.
            time.sleep(0.2)

            def _value_after(flag):
                return args[args.index(flag) + 1]

            command = args[0]
            if command != "import":
                raise AssertionError(f"unexpected stub engine command: {command}")
            source_path = Path(args[1])
            output_path = Path(_value_after("--output"))
            raw_dir = Path(_value_after("--raw-dir"))
            if "fail" in source_path.name:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="engine: stub failure\n"
                )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            raw_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"stub": "import"}) + "\n", encoding="utf-8")
            (raw_dir / "raw.json").write_text("{}\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        finally:
            with guard:
                in_progress.clear()

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Concurrent import"})
        run_id = run["id"]

        results: dict[str, tuple[int, dict]] = {}

        def do_import(key, filename):
            results[key] = _request(
                server,
                f"/api/runs/{run_id}/import",
                "POST",
                {"filename": filename, "content_base64": _b64(b"TY  - JOUR\nER  -\n")},
            )

        t1 = threading.Thread(target=do_import, args=("ok", "records-ok.ris"))
        t2 = threading.Thread(target=do_import, args=("fail", "records-fail.ris"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive(), "concurrent import requests did not finish"

        assert not overlap_detected.is_set(), "concurrent imports on the same run interleaved"

        _, record = _request(server, f"/api/runs/{run_id}")
        run_dir = tmp_path / "runs" / run_id
        frozen_exists = (run_dir / "frozen.json").is_file()
        raw_exists = (run_dir / "raw" / "raw.json").is_file()
        if record["stages"]["import"] == "complete":
            assert frozen_exists and raw_exists, record
        else:
            assert not frozen_exists and not raw_exists, record
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_bridge_initialize_run_invokes_expected_cli_shape(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md stage 1 ("operator shell"): `initialize_run` is
    the one bridge method every run creation exercises (`RunStore.create`'s
    HTTP caller in `server.py` invokes it immediately after a new run
    directory is made), and it is already covered indirectly by
    `test_create_run_initializes_engine_contract` and
    `test_stage_one_operator_shell_runs_against_real_engine_cli`. Every
    other pipeline bridge method (`import_records`, `dedup_records`,
    `lock_protocol`, `screen_records`, `package_run`, `replay_run`,
    `extract_record`, `rob_assessment`, `synthesize`) has its own direct,
    `EngineBridge`-layer `..._invokes_expected_cli_shape` /
    `..._raises_bridge_error_on_nonzero_exit` pair immediately below,
    independent of the HTTP-layer tests that exercise the same methods
    indirectly through `server.py`'s actions -- `initialize_run` had no
    equivalent pair of its own. This closes that narrow coverage-symmetry
    gap the same way it was already closed for the stage 7-9 bridge
    methods: a direct assertion on the positional-argument CLI shape
    `initialize_run` actually sends to the engine, independent of the HTTP
    layer above it. It exercises no new server-side or bridge-side code
    path -- `EngineBridge.initialize_run` was already correct and already
    covered indirectly."""
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    bridge.initialize_run(run_dir)

    assert captured["args"] == ["init", str(run_dir)]


def test_bridge_initialize_run_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad run directory\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad run directory"):
        bridge.initialize_run(tmp_path / "run")


def test_bridge_import_records_invokes_expected_cli_shape(tmp_path, monkeypatch):
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    source_path = tmp_path / "records.ris"
    source_path.write_text("TY  - JOUR\nER  -\n", encoding="utf-8")
    output_path = tmp_path / "frozen.json"
    raw_dir = tmp_path / "raw"

    bridge.import_records(
        source_path,
        source_label="records.ris",
        output_path=output_path,
        raw_dir=raw_dir,
    )

    args = captured["args"]
    assert args[0] == "import"
    assert args[1] == str(source_path)
    assert args[args.index("--source") + 1] == "records.ris"
    assert args[args.index("--output") + 1] == str(output_path)
    assert args[args.index("--raw-dir") + 1] == str(raw_dir)
    assert "--format" not in args


def test_bridge_import_records_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad source file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad source file"):
        bridge.import_records(
            tmp_path / "records.ris",
            source_label="records.ris",
            output_path=tmp_path / "frozen.json",
            raw_dir=tmp_path / "raw",
        )


def test_bridge_dedup_records_invokes_expected_cli_shape(tmp_path, monkeypatch):
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    frozen_path = tmp_path / "frozen.json"
    frozen_path.write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "dedup.json"

    bridge.dedup_records(frozen_path, output_path=output_path)

    args = captured["args"]
    assert args[0] == "dedup"
    assert args[1] == str(frozen_path)
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_dedup_records_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad frozen file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad frozen file"):
        bridge.dedup_records(tmp_path / "frozen.json", output_path=tmp_path / "dedup.json")


def test_bridge_lock_protocol_invokes_expected_cli_shape(tmp_path, monkeypatch):
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text("{}\n", encoding="utf-8")
    procedure_path = tmp_path / "procedure.json"
    procedure_path.write_text("{}\n", encoding="utf-8")
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}\n", encoding="utf-8")
    dedup_path = tmp_path / "dedup.json"
    dedup_path.write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "lock.json"

    bridge.lock_protocol(
        protocol_path,
        procedure_path,
        schema_path,
        dedup_path,
        output_path=output_path,
    )

    args = captured["args"]
    assert args[0] == "lock"
    assert args[args.index("--protocol") + 1] == str(protocol_path)
    assert args[args.index("--procedure") + 1] == str(procedure_path)
    assert args[args.index("--schema") + 1] == str(schema_path)
    assert args[args.index("--input") + 1] == str(dedup_path)
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_lock_protocol_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad dedup file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad dedup file"):
        bridge.lock_protocol(
            tmp_path / "protocol.json",
            tmp_path / "procedure.json",
            tmp_path / "schema.json",
            tmp_path / "dedup.json",
            output_path=tmp_path / "lock.json",
        )


def test_bridge_screen_records_invokes_expected_cli_shape(tmp_path, monkeypatch):
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    input_path = tmp_path / "dedup.json"
    input_path.write_text("{}\n", encoding="utf-8")
    lock_path = tmp_path / "lock.json"
    lock_path.write_text("{}\n", encoding="utf-8")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text("{}\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    output_path = tmp_path / "screen.json"

    bridge.screen_records(
        input_path,
        lock_path,
        decisions_path,
        cache_dir,
        human_adjudication="off",
        output_path=output_path,
    )

    args = captured["args"]
    assert args[0] == "screen"
    assert args[1] == str(input_path)
    assert args[args.index("--lock") + 1] == str(lock_path)
    assert args[args.index("--decisions") + 1] == str(decisions_path)
    assert args[args.index("--cache-dir") + 1] == str(cache_dir)
    assert args[args.index("--human-adjudication") + 1] == "off"
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_screen_records_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad lock file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad lock file"):
        bridge.screen_records(
            tmp_path / "dedup.json",
            tmp_path / "lock.json",
            tmp_path / "decisions.json",
            tmp_path / "cache",
            human_adjudication="off",
            output_path=tmp_path / "screen.json",
        )


def test_bridge_package_run_invokes_expected_cli_shape(tmp_path, monkeypatch):
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    output_path = tmp_path / "package.json"

    screen_path = run_dir / "screen.json"
    bridge.package_run(screen_path, output_path=output_path)

    args = captured["args"]
    assert args[0] == "package"
    assert args[1] == str(screen_path)
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_package_run_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad run directory\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad run directory"):
        bridge.package_run(tmp_path / "screen.json", output_path=tmp_path / "package.json")


def test_bridge_package_run_wraps_real_engine_screening_package_into_portable_manifest(
    tmp_path, monkeypatch
):
    """`package_run`'s own compatibility-adapter logic -- wrapping the real
    engine's `screening-package-v1`-versioned `package` output into this
    WebUI's portable `ese-webui-package-v1` manifest/`artifacts_base64`
    payload (see `bridge.py` `package_run`) -- is the actual "Compatibility"
    (docs/UI_BUILD_LOOP.md stage 6) contract this bridge method exists to
    satisfy: the real engine only produces a screening-package artifact and
    knows nothing about this WebUI's own four-artifact manifest/base64
    contract, so `package_run` itself must adapt one into the other. Nothing
    in this package's always-run offline stub suite previously exercised
    that adapter logic directly: `_install_stub_bridge`'s own `command ==
    "package"` branch writes `package.json` already in the *final* portable
    shape (with no `version` field at all), so `package_run`'s own
    `payload.get("version") != "screening-package-v1"` check always
    short-circuits to a no-op *before* its wrapping logic ever runs in every
    existing stub-driven test, and
    `test_bridge_package_run_invokes_expected_cli_shape` above never writes
    any output file at all, so it only exercises the CLI-argument shape, not
    this method's own post-invocation adapter behavior. Only the
    isolated-checkout-gated real-engine tests (which this offline suite does
    not depend on, and which this session's tool set cannot run) could have
    exercised the real wrapping path at all.

    This calls `package_run` directly (no HTTP server, no stub bridge
    dispatch) against a hand-built run directory containing the four
    `PACKAGE_ARTIFACT_NAMES` artifacts, with `EngineBridge._invoke` replaced
    by a fake that writes a genuinely `screening-package-v1`-shaped payload
    -- the real engine's documented `package` output shape -- and asserts
    `package_run` rewrites `output_path` into the portable
    `ese-webui-package-v1` shape: the original engine payload preserved
    verbatim under `engine_package`, a `manifest` whose sha256 digests
    actually match the run directory's on-disk artifacts (not merely
    well-shaped hex strings), and an `artifacts_base64` field whose entries
    decode back to those exact same bytes."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    contents: dict[str, bytes] = {}
    for name in PACKAGE_ARTIFACT_NAMES:
        data = json.dumps({"artifact": name}).encode("utf-8") + b"\n"
        (run_dir / name).write_bytes(data)
        contents[name] = data

    engine_payload = {"version": "screening-package-v1", "stub": "real-engine-package"}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        output_path = Path(args[args.index("--output") + 1])
        output_path.write_text(json.dumps(engine_payload) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    output_path = tmp_path / "package.json"

    bridge.package_run(run_dir / "screen.json", output_path=output_path)

    wrapped = json.loads(output_path.read_text(encoding="utf-8"))
    assert wrapped["version"] == "ese-webui-package-v1", wrapped
    assert wrapped["engine_package"] == engine_payload, wrapped
    assert set(wrapped["manifest"]) == set(PACKAGE_ARTIFACT_NAMES), wrapped
    for name in PACKAGE_ARTIFACT_NAMES:
        assert wrapped["manifest"][name] == hashlib.sha256(contents[name]).hexdigest(), name
        assert base64.b64decode(wrapped["artifacts_base64"][name]) == contents[name], name


def test_bridge_package_run_leaves_unrecognized_engine_package_shape_untouched(
    tmp_path, monkeypatch
):
    """The complementary path to the wrapping test above, also previously
    uncovered by any test in this file: when the engine's `package` output
    does not report `version == "screening-package-v1"` (an unrecognized or
    future engine package shape), `package_run` must leave that output
    exactly as the engine wrote it rather than silently reinterpreting a
    shape it does not actually recognize as if it were the one contract it
    knows how to adapt -- the same fail-closed, "do not fabricate
    compatibility" posture this bridge already applies elsewhere for
    genuine failures, just never previously exercised for this specific
    branch."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in PACKAGE_ARTIFACT_NAMES:
        (run_dir / name).write_bytes(b"{}\n")

    unrecognized_payload = {"version": "some-other-shape-v2", "stub": "unrecognized"}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        output_path = Path(args[args.index("--output") + 1])
        output_path.write_text(json.dumps(unrecognized_payload) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    output_path = tmp_path / "package.json"

    bridge.package_run(run_dir / "screen.json", output_path=output_path)

    untouched = json.loads(output_path.read_text(encoding="utf-8"))
    assert untouched == unrecognized_payload, untouched


def test_bridge_replay_run_invokes_expected_cli_shape(tmp_path, monkeypatch):
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    package_path = tmp_path / "package.json"
    package_path.write_text("{}\n", encoding="utf-8")
    output_dir = tmp_path / "replay"

    bridge.replay_run(
        package_path,
        lock_path=tmp_path / "lock.json",
        dedup_path=tmp_path / "dedup.json",
        cache_dir=tmp_path / "cache",
        output_dir=output_dir,
    )

    args = captured["args"]
    assert args[0] == "replay"
    assert args[1] == str(package_path)
    assert args[args.index("--lock") + 1] == str(tmp_path / "lock.json")
    assert args[args.index("--dedup") + 1] == str(tmp_path / "dedup.json")
    assert args[args.index("--cache-dir") + 1] == str(tmp_path / "cache")


def test_bridge_replay_run_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad package file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad package file"):
        bridge.replay_run(
            tmp_path / "package.json",
            lock_path=tmp_path / "lock.json",
            dedup_path=tmp_path / "dedup.json",
            cache_dir=tmp_path / "cache",
            output_dir=tmp_path / "replay",
        )


def test_bridge_replay_run_invokes_replay_cli_against_extracted_engine_package(tmp_path, monkeypatch):
    """`replay_run`'s own complementary compatibility-adapter logic to
    `package_run`'s wrapping (see
    `test_bridge_package_run_wraps_real_engine_screening_package_into_portable_manifest`
    above): when a run's `package.json` carries this WebUI's portable
    `ese-webui-package-v1` shape with a real engine payload embedded under
    `engine_package`, `replay_run` must extract that embedded payload back
    out into its own standalone temporary file and pass *that* file -- not
    the WebUI's own wrapper file, which the real engine's `replay` CLI does
    not understand -- as `replay`'s positional argument, then remove that
    temporary file again once the invocation completes.

    Nothing in this package's always-run offline stub suite previously
    exercised this unwrap-before-invoke behavior directly:
    `test_stub_replay_reconstructs_solely_from_package_content` drives
    `replay_run` only through `_install_stub_bridge`'s stub-shaped
    `package.json`, which has no `engine_package` field at all (the stub
    already writes the *final* portable shape directly, the same reason
    `package_run`'s own `version != "screening-package-v1"` no-op branch
    was previously uncovered), and
    `test_bridge_replay_run_invokes_expected_cli_shape` above writes a bare
    `{}` package file with no `engine_package` field either, so it only
    proves the CLI *flag* shape, never this unwrap behavior. Only the
    isolated-checkout-gated real-engine tests (which this offline suite
    does not depend on, and which this session's tool set cannot run) could
    have exercised it.

    This builds a `package.json` in the actual wrapped shape `package_run`
    produces (`engine_package` a real-engine-shaped payload,
    `artifacts_base64` the four `PACKAGE_ARTIFACT_NAMES` artifacts'
    base64-encoded bytes) and replaces `EngineBridge._invoke` with a fake
    that records the exact file `replay_run` passed as its positional
    argument. It asserts that file is a *different* path from
    `package.json` itself, that its content is exactly the embedded
    `engine_package` payload (not the outer wrapper), and that it no longer
    exists once `replay_run` returns -- proving the temporary file is
    cleaned up rather than left behind -- while the reconstructed
    `PACKAGE_ARTIFACT_NAMES` artifacts under `output_dir` still match the
    embedded `artifacts_base64` content byte for byte."""
    engine_package = {"version": "screening-package-v1", "stub": "real-engine-package"}
    contents = {
        name: json.dumps({"artifact": name}).encode("utf-8") + b"\n"
        for name in PACKAGE_ARTIFACT_NAMES
    }
    package_payload = {
        "version": "ese-webui-package-v1",
        "engine_package": engine_package,
        "manifest": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        "artifacts_base64": {
            name: base64.b64encode(data).decode("ascii") for name, data in contents.items()
        },
    }
    package_path = tmp_path / "package.json"
    package_path.write_text(json.dumps(package_payload), encoding="utf-8")

    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        positional_path = Path(args[1])
        captured["positional_path"] = positional_path
        captured["positional_content"] = positional_path.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    output_dir = tmp_path / "replay"

    bridge.replay_run(
        package_path,
        lock_path=tmp_path / "lock.json",
        dedup_path=tmp_path / "dedup.json",
        cache_dir=tmp_path / "cache",
        output_dir=output_dir,
    )

    assert captured["args"][0] == "replay", captured
    assert captured["positional_path"] != package_path, captured
    assert json.loads(captured["positional_content"]) == engine_package, captured
    assert not captured["positional_path"].exists(), (
        "replay_run must remove the temporary extracted engine_package file "
        "once the replay invocation completes, not leave it behind"
    )

    for name, data in contents.items():
        assert (output_dir / name).read_bytes() == data, name


def test_bridge_package_run_raises_bridge_error_on_non_object_engine_output(
    tmp_path, monkeypatch
):
    """A further "Compatibility" gap alongside the wrapping/pass-through
    tests above: `package_run`'s own post-invocation adapter logic reads
    the engine's `--output` file with a bare `json.loads(...)` and then
    calls `.get("version")` on the result. Every existing test here (and
    the always-run offline stub in `_install_stub_bridge`) only ever writes
    a JSON *object* to that file, so this bridge method's behavior when the
    engine instead writes well-formed JSON that is not an object (a bare
    list, for example) was never exercised: `.get` on a non-dict value
    raises an unhandled `AttributeError`, not the `BridgeError` this bridge
    otherwise fails closed with for every other malformed-output case (see
    `read_json`'s own `isinstance(value, dict)` check, which this method's
    JSON read did not previously share).

    This feeds a syntactically valid JSON array as the engine's `--output`
    content and asserts `package_run` raises `BridgeError` (not
    `AttributeError`) -- proving this adapter fails closed on a genuinely
    malformed engine response shape instead of crashing."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in PACKAGE_ARTIFACT_NAMES:
        (run_dir / name).write_bytes(b"{}\n")

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        output_path = Path(args[args.index("--output") + 1])
        output_path.write_text("[1, 2, 3]\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    output_path = tmp_path / "package.json"

    with pytest.raises(BridgeError, match="package"):
        bridge.package_run(run_dir / "screen.json", output_path=output_path)


def test_bridge_replay_run_raises_bridge_error_on_non_object_package_file(
    tmp_path, monkeypatch
):
    """The complementary gap in `replay_run`'s own package-file read: it
    also loads `package_path` with a bare `json.loads(...)` and then calls
    `.get("engine_package")` on the result, sharing the same missing
    `isinstance(..., dict)` guard `package_run` lacked above. This feeds a
    syntactically valid JSON array as the run's `package.json` content and
    asserts `replay_run` raises `BridgeError` (not `AttributeError`) before
    ever invoking the engine CLI -- the fake `_invoke` below raises if
    called at all, so a passing test also proves this fails closed before
    any subprocess is launched."""
    package_path = tmp_path / "package.json"
    package_path.write_text("[1, 2, 3]\n", encoding="utf-8")

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        raise AssertionError(
            "replay_run must not invoke the engine CLI on a malformed package file"
        )

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="package"):
        bridge.replay_run(
            package_path,
            lock_path=tmp_path / "lock.json",
            dedup_path=tmp_path / "dedup.json",
            cache_dir=tmp_path / "cache",
            output_dir=tmp_path / "replay",
        )


def test_bridge_package_run_raises_bridge_error_on_malformed_json_engine_output(
    tmp_path, monkeypatch
):
    """A further "Compatibility" gap alongside the non-object guard above:
    `package_run`'s post-invocation read of the engine's `--output` file
    called `json.loads(...)` directly with no `try`/`except`, unlike
    `read_json` (used everywhere else in this bridge for artifact reads),
    which explicitly catches `json.JSONDecodeError` and re-raises it as a
    clean `BridgeError`. Every existing test here only ever writes
    syntactically valid JSON to that file (either a well-formed object or,
    for the sibling test above, a well-formed array), so genuinely malformed
    JSON -- syntactically invalid text, as a real engine's `--output` file
    could contain if it crashed mid-write or emitted a non-JSON error page --
    was never exercised: it would raise an unhandled `json.JSONDecodeError`
    instead of the `BridgeError` this bridge otherwise fails closed with for
    every other malformed-output case.

    This feeds syntactically invalid text as the engine's `--output` content
    and asserts `package_run` raises `BridgeError` (not `JSONDecodeError`) --
    proving this adapter fails closed on genuinely malformed engine output,
    not merely well-formed-but-wrong-shaped output."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in PACKAGE_ARTIFACT_NAMES:
        (run_dir / name).write_bytes(b"{}\n")

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        output_path = Path(args[args.index("--output") + 1])
        output_path.write_text("{not valid json", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    output_path = tmp_path / "package.json"

    with pytest.raises(BridgeError, match="package"):
        bridge.package_run(run_dir / "screen.json", output_path=output_path)


def test_bridge_replay_run_raises_bridge_error_on_malformed_json_package_file(
    tmp_path, monkeypatch
):
    """The complementary gap in `replay_run`'s own package-file read: it
    also called `json.loads(package_path.read_text(...))` directly, with no
    `try`/`except` around genuinely invalid JSON syntax, sharing the same
    missing guard `package_run` lacked above. This feeds syntactically
    invalid text as the run's `package.json` content and asserts
    `replay_run` raises `BridgeError` (not `JSONDecodeError`) before ever
    invoking the engine CLI -- the fake `_invoke` below raises if called at
    all, so a passing test also proves this fails closed before any
    subprocess is launched."""
    package_path = tmp_path / "package.json"
    package_path.write_text("{not valid json", encoding="utf-8")

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        raise AssertionError(
            "replay_run must not invoke the engine CLI on a malformed package file"
        )

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="package"):
        bridge.replay_run(
            package_path,
            lock_path=tmp_path / "lock.json",
            dedup_path=tmp_path / "dedup.json",
            cache_dir=tmp_path / "cache",
            output_dir=tmp_path / "replay",
        )


def test_bridge_extract_record_invokes_expected_cli_shape(tmp_path, monkeypatch):
    """Stage 7's `extract_record` bridge method has no dedicated CLI-shape
    unit test of its own (unlike `import_records`/`dedup_records`/
    `lock_protocol`/`screen_records`/`package_run`/`replay_run` immediately
    above, each of which is exercised directly at the `EngineBridge` layer
    in addition to through the HTTP `extract`/`rob`/`synthesize` actions'
    own stub-driven tests). docs/UI_BUILD_LOOP.md phase 10 ("Full offline
    stack") drives `extract`/`rob`/`synthesize` through this exact bridge
    method as part of its pinned real-engine and stub contract checks, so
    this closes that coverage gap the same way it is already closed for
    every earlier pipeline action: a direct assertion on the positional
    argument and flag shape `extract_record` actually sends to the engine
    CLI, independent of the HTTP layer above it."""
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    source_path = tmp_path / "source.txt"
    source_path.write_text("Sample size was 240 participants.\n", encoding="utf-8")
    fields_path = tmp_path / "fields.json"
    fields_path.write_text("[]\n", encoding="utf-8")
    output_path = tmp_path / "extraction.json"

    bridge.extract_record(
        source_path,
        record_id="rec-1",
        provenance_file="extraction/source/source.txt",
        fields_path=fields_path,
        output_path=output_path,
    )

    args = captured["args"]
    assert args[0] == "extract"
    assert args[1] == str(source_path)
    assert args[args.index("--record-id") + 1] == "rec-1"
    assert args[args.index("--provenance-file") + 1] == "extraction/source/source.txt"
    assert args[args.index("--fields") + 1] == str(fields_path)
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_extract_record_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad source file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad source file"):
        bridge.extract_record(
            tmp_path / "source.txt",
            record_id="rec-1",
            provenance_file="extraction/source/source.txt",
            fields_path=tmp_path / "fields.json",
            output_path=tmp_path / "extraction.json",
        )


def test_bridge_rob_assessment_invokes_expected_cli_shape(tmp_path, monkeypatch):
    """Same coverage gap as `test_bridge_extract_record_invokes_expected_cli_shape`,
    one stage further: stage 8's `rob_assessment` bridge method had no
    direct CLI-shape unit test of its own."""
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "rob.json"

    bridge.rob_assessment(spec_path, output_path=output_path)

    args = captured["args"]
    assert args[0] == "rob"
    assert args[1] == str(spec_path)
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_rob_assessment_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad spec file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad spec file"):
        bridge.rob_assessment(tmp_path / "spec.json", output_path=tmp_path / "rob.json")


def test_bridge_synthesize_invokes_expected_cli_shape(tmp_path, monkeypatch):
    """Same coverage gap as `test_bridge_extract_record_invokes_expected_cli_shape`,
    two stages further: stage 9's `synthesize` bridge method had no direct
    CLI-shape unit test of its own."""
    captured = {}

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "synthesis.json"

    bridge.synthesize(spec_path, output_path=output_path)

    args = captured["args"]
    assert args[0] == "synthesize"
    assert args[1] == str(spec_path)
    assert args[args.index("--output") + 1] == str(output_path)


def test_bridge_synthesize_raises_bridge_error_on_nonzero_exit(tmp_path, monkeypatch):
    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="engine: bad spec file\n")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)
    bridge = EngineBridge(tmp_path)

    with pytest.raises(BridgeError, match="bad spec file"):
        bridge.synthesize(tmp_path / "spec.json", output_path=tmp_path / "synthesis.json")


def test_artifact_listing_and_credential_isolation(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Artifacts"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        names = {item["name"] for item in payload["artifacts"]}
        assert "doctor.json" in names
        assert "protocol.json" in names
        assert "run.json" not in names

        # Stage 3 ("deduplication and lock", docs/UI_BUILD_LOOP.md) adds a
        # deterministic sha256 content hash to every listed artifact, not
        # only to dedup/lock artifacts specifically.
        by_name = {item["name"]: item for item in payload["artifacts"]}
        run_dir = tmp_path / "runs" / run_id
        for name, item in by_name.items():
            on_disk = (run_dir / name).read_bytes()
            assert item["sha256"] == hashlib.sha256(on_disk).hexdigest(), name
            assert item["bytes"] == len(on_disk), name

        _, listing = _request(server, f"/api/runs/{run_id}/artifacts")
        assert {item["name"] for item in listing["artifacts"]} == names

        status, health = _request(server, "/api/health")
        assert health["credentials_loaded"] is False
        assert health["credentials_isolated"] is True
        assert "PATH" in health["engine_env_allowlist"]
    finally:
        server.shutdown()
        thread.join()


def test_doctor_warnings_are_reported_not_swallowed_as_a_bridge_error(tmp_path, monkeypatch):
    """This is the stage-1 regression this fix targets: the engine's
    `doctor` reports readiness problems (for example unrelated untracked
    planning files) via `passed: false` in its JSON report *and* a nonzero
    exit code *and* stderr output, all at once. `doctor` is a diagnostic,
    not a pass/fail bridge command -- the WebUI's job is to run it and
    hand the operator the structured result, not to translate "the
    diagnostic found a problem" into "the HTTP request failed" and lose
    the report. Before this fix, EngineBridge.run raised BridgeError on
    any nonzero exit, so the WebUI returned HTTP 400 with only a bare
    string and the full structured report (and the fact that the action
    itself ran to completion) was discarded."""
    _install_stub_bridge(
        monkeypatch,
        doctor_passed=False,
        doctor_returncode=1,
        doctor_stderr="warning: 2 untracked planning files under docs/\n",
    )
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Warnings present"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})

        # Running the diagnostic is itself a successful WebUI action, even
        # though the diagnostic it ran reported a readiness problem.
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete", payload

        # The failure information the engine produced must be preserved
        # verbatim, not hidden or falsely turned into a pass.
        doctor = payload["doctor"]
        assert doctor["passed"] is False, doctor
        assert doctor["exit_code"] == 1, doctor
        assert "untracked planning files" in doctor["stderr"], doctor
        assert doctor["report"]["passed"] is False, doctor

        names = {item["name"] for item in payload["artifacts"]}
        assert "doctor.json" in names
    finally:
        server.shutdown()
        thread.join()


def test_doctor_reports_passing_result_when_engine_reports_passed(tmp_path, monkeypatch):
    _install_stub_bridge(monkeypatch, doctor_passed=True, doctor_returncode=0)
    server, thread = _server(tmp_path)
    try:
        _, run = _request(server, "/api/runs", "POST", {"name": "Clean run"})
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["doctor"]["passed"] is True, payload
        assert payload["doctor"]["exit_code"] == 0, payload
    finally:
        server.shutdown()
        thread.join()


def test_doctor_execution_failure_without_a_report_still_surfaces_as_an_error(tmp_path, monkeypatch):
    """A `doctor` invocation that never produces a structured report at all
    (a real crash, a bad flag, a timeout) is a genuine bridge failure, not
    a diagnostic result -- unlike ordinary readiness warnings, this must
    still surface as an error with the engine's stderr/exit status
    preserved, not a false 200."""

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        return subprocess.CompletedProcess(
            args, 2, stdout="", stderr="engine crashed before writing a report\n"
        )

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)

    with pytest.raises(BridgeError, match="engine crashed before writing a report"):
        EngineBridge(tmp_path).run_diagnostic(
            ["doctor", "--output", str(tmp_path / "doctor.json")],
            output_path=tmp_path / "doctor.json",
        )


def test_run_diagnostic_does_not_return_a_stale_report_from_a_previous_invocation(tmp_path, monkeypatch):
    """A report file left over at `output_path` from an earlier `doctor`
    invocation must never be returned as this invocation's result. If the
    engine subprocess crashes (or otherwise exits without writing a fresh
    report) while a stale report from a prior call still sits at
    `output_path`, `run_diagnostic` must fail closed exactly like a crash
    with no report at all -- not silently hand back the old, unrelated
    report as if it reflected the current run."""
    output_path = tmp_path / "doctor.json"
    output_path.write_text(json.dumps({"passed": True, "stub": "stale-report"}), encoding="utf-8")

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        # Simulates a genuine crash: the subprocess exits nonzero and never
        # writes a new report, leaving only the stale file above (if the
        # bridge fails to invalidate it first).
        return subprocess.CompletedProcess(
            args, 2, stdout="", stderr="engine crashed before writing a report\n"
        )

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)

    with pytest.raises(BridgeError, match="engine crashed before writing a report"):
        EngineBridge(tmp_path).run_diagnostic(
            ["doctor", "--output", str(output_path)],
            output_path=output_path,
        )


def test_run_diagnostic_rejects_a_report_missing_the_passed_field(tmp_path, monkeypatch):
    """The engine's diagnostic contract requires a structured report with a
    `passed` field. A report that omits it entirely is not a value this
    bridge may guess at (for example by defaulting to False or True) --
    that would either hide a real pass or fabricate one. It must fail
    closed with a BridgeError instead."""
    output_path = tmp_path / "doctor.json"

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        output_path.write_text(json.dumps({"stub": "no-passed-field"}), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)

    with pytest.raises(BridgeError, match="missing required 'passed' field"):
        EngineBridge(tmp_path).run_diagnostic(
            ["doctor", "--output", str(output_path)],
            output_path=output_path,
        )


def test_run_diagnostic_rejects_a_non_boolean_passed_field(tmp_path, monkeypatch):
    """A malformed report where `passed` is not an actual JSON boolean (for
    example the string "false") must never be silently coerced -- `bool("false")`
    is True in Python, so a naive `bool(report.get("passed"))` conversion
    would turn a malformed failure report into a false pass. This must
    fail closed with a BridgeError instead of ever returning `passed=True`
    for such a report."""
    output_path = tmp_path / "doctor.json"

    def fake_invoke(self, args):  # noqa: ANN001 - matches EngineBridge._invoke signature
        output_path.write_text(
            json.dumps({"passed": "false", "stub": "string-passed-field"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(EngineBridge, "_invoke", fake_invoke)

    with pytest.raises(BridgeError, match="must be a JSON boolean"):
        EngineBridge(tmp_path).run_diagnostic(
            ["doctor", "--output", str(output_path)],
            output_path=output_path,
        )


def test_engine_bridge_only_forwards_allowlisted_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "definitely-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "also-secret")

    bridge = EngineBridge(tmp_path)
    env = bridge.subprocess_environment()

    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["PYTHONPATH"] == str(bridge.engine_root)
    assert bridge.credentials_isolated() is True


def test_home_like_variables_are_redirected_not_forwarded(monkeypatch, tmp_path):
    """Stripping credential-shaped variable *names* is not enough: HOME,
    USERPROFILE, APPDATA, and LOCALAPPDATA are non-credential-shaped names
    whose host *values* are exactly where default credential files live
    (~/.aws/credentials, ~/.netrc, %APPDATA%\\gcloud, etc.). The bridge must
    substitute a redirected, credential-free value for these rather than
    forwarding the host's real one."""
    monkeypatch.setenv("HOME", "/private/host-home-with-credentials")
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\host-with-credentials")
    monkeypatch.setenv("APPDATA", "C:\\Users\\host-with-credentials\\AppData\\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\host-with-credentials\\AppData\\Local")

    bridge = EngineBridge(tmp_path)
    env = bridge.subprocess_environment()

    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        assert env[key] != os.environ[key], key
    # The redirected value must be a real, empty, bridge-owned directory,
    # not merely an arbitrary non-matching string.
    isolated_home = Path(env["HOME"])
    assert isolated_home.is_dir()
    assert list(isolated_home.iterdir()) == []
    assert bridge.credentials_isolated() is True


def test_credentials_isolated_detects_unredirected_home_value(monkeypatch, tmp_path):
    """credentials_isolated() must fail closed if a future change ever
    forwards the host's real HOME-like value unchanged, not just check
    variable *names* (the exact gap flagged in review)."""
    bridge = EngineBridge(tmp_path)
    host_home = os.environ.get("HOME", "/home/host")
    monkeypatch.setenv("HOME", host_home)
    monkeypatch.setattr(
        EngineBridge,
        "subprocess_environment",
        lambda self: {"PATH": os.environ.get("PATH", ""), "HOME": host_home},
    )

    assert bridge.credentials_isolated() is False


def test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays_with_post_package_byte_stability_check(
    tmp_path, monkeypatch
):
    """docs/UI_BUILD_LOOP.md phase 10 ("Full offline stack"): a second
    deterministic-stub counterpart to
    `test_full_offline_stack_runs_stages_one_through_nine_and_replays`
    below, adding post-package/replay byte-stability and full-stage-summary
    checks on top of the coverage
    `test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays`
    (defined earlier in this file, in the stage-9 test block) already
    provides.

    This test drives the exact same scope the real-engine test drives --
    run creation, `doctor`, `import`, `dedup`, `lock`, the
    `human_adjudication` toggle, `screen` (resolving via `adjudicate` if
    the stub's cached screening result happens to abstain), source-anchored
    `extract`, bounded `rob`, single-study `synthesize`, `package`, and
    `replay` -- entirely through the deterministic stub bridge
    (`_install_stub_bridge`) every other always-run test in this file
    already uses. It checks the same cross-phase provenance (one included
    record id surviving screening, extraction, RoB, and synthesis
    unmodified) and the same post-extraction/RoB/synthesis byte-stable
    `package`/`replay` contract.

    A prior revision of this file gave this function the exact same name
    as the pre-existing `test_stub_full_offline_stack_runs_stages_one_through_nine_and_replays`
    defined earlier in this module, and this docstring used to claim that
    no function under that name existed yet anywhere in the module and
    that this was "the first real instance" of this coverage. Both claims
    were false: that earlier function already existed and already ran, and
    this later definition of the exact same name silently shadowed it in
    the module namespace pytest collects tests from, so only this
    definition was ever actually collected -- the earlier one became dead
    code. That is exactly the kind of undocumented regression this
    document elsewhere warns against, reintroduced by the very edit that
    claimed (about a separate, at-the-time-fictitious instance of it) to
    have fixed it. This function has been renamed to its own unique name to
    restore collection and execution of both tests; see
    `docs/UI_BUILD_LOOP.md`'s Phase 10 status section for the corrected
    record."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        status, run = _request(
            server, "/api/runs", "POST", {"name": "Full offline stack (stub)"}
        )
        assert status == 201, run
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {
                "filename": "full-stack-stub.ris",
                "content_base64": _b64(
                    b"TY  - JOUR\r\n"
                    b"TI  - A minimal record for full offline stack stub verification\r\n"
                    b"AU  - Doe, Jane\r\n"
                    b"PY  - 2024\r\n"
                    b"ER  -  \r\n"
                ),
            },
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload

        # Turn human adjudication on *before* screening so that, if the
        # stub's cached screening result happens to abstain on a locked
        # record (a test can configure this via `_install_stub_bridge`'s
        # `screen_decisions`; the default configuration used here does
        # not), the "blocked" branch below is genuinely reachable and this
        # run genuinely exercises `adjudicate` too, mirroring the
        # real-engine test's same precaution.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        screen_status = payload["run"]["stages"]["screen"]
        assert screen_status in ("complete", "blocked"), payload

        if screen_status == "blocked":
            for record_id in payload["screen"]["remaining"]:
                status, payload = _request(
                    server,
                    f"/api/runs/{run_id}/adjudicate",
                    "POST",
                    {"id": record_id, "decision": "include"},
                )
                assert status == 200, payload
            assert payload["run"]["stages"]["screen"] == "complete", payload

        run_dir = tmp_path / "runs" / run_id
        screen_artifact = json.loads((run_dir / "screen.json").read_text(encoding="utf-8"))
        decisions = screen_artifact.get("decisions") or [
            {"id": item["record_id"], "decision": item["decision"]}
            for item in screen_artifact.get("results", [])
        ]

        # Resolve each record's *effective* decision the same way
        # `_screening_state()` (server.py) resolves the blocking queue: a
        # human `adjudicate` call overrides that record's raw engine
        # decision. Using the raw `screen.json` decision alone here could
        # let an all-`exclude` raw screening result silently select an
        # excluded record below, which would prove nothing about
        # provenance from an *included* record.
        adjudications_path = run_dir / "adjudications.json"
        adjudications = (
            json.loads(adjudications_path.read_text(encoding="utf-8"))
            if adjudications_path.is_file()
            else {}
        )
        effective_decisions = {
            item["id"]: adjudications.get(item["id"], item["decision"])
            for item in decisions
        }
        included_ids = [
            record_id
            for record_id, decision in effective_decisions.items()
            if decision == "include"
        ]
        assert included_ids, (
            "no record resolved to an effective 'include' decision; "
            "cross-phase provenance from an included record cannot be "
            f"established: {effective_decisions}"
        )
        record_id = included_ids[0]

        # --- Stage 7: source-anchored extraction -------------------------
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete", payload
        extraction_record_hash = payload["extraction"]["record_hash"]
        extraction_artifact = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        assert extraction_artifact["record_id"] == record_id, extraction_artifact

        # --- Stage 8: bounded risk-of-bias --------------------------------
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/rob",
            "POST",
            _rob_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload
        assert payload["rob"]["record_id"] == record_id, payload

        # --- Stage 9: bounded single-study synthesis ----------------------
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload
        assert payload["synthesis"]["status"] == "calculated", payload
        assert payload["synthesis"]["record_id"] == record_id, payload

        # Snapshot the exact on-disk bytes of the three artifacts that are
        # deliberately *outside* `PACKAGE_ARTIFACT_NAMES` (extraction/RoB/
        # synthesis) before `package`/`replay` run below. Comparing full
        # bytes -- not merely re-checking a hash field embedded inside the
        # JSON itself, and not merely re-checking the files still exist --
        # is the only way to actually prove `package`/`replay` do not
        # disturb them.
        pre_package_extraction_bytes = (run_dir / "extraction.json").read_bytes()
        pre_package_rob_bytes = (run_dir / "rob.json").read_bytes()
        pre_package_synthesis_bytes = (run_dir / "synthesis.json").read_bytes()

        # --- Stage 5: package/replay, taken *after* extraction/RoB/
        # synthesis have also written their own artifacts onto this run --
        # must still bundle and reproduce exactly the four
        # frozen/dedup/lock/screen artifacts, byte for byte.
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        manifest = payload["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload
        assert payload["replay"]["byte_stable"] is True, payload

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["packaged"] is True, comparison
        assert comparison["replayed"] is True, comparison
        assert comparison["byte_stable"] is True, comparison

        # Cross-phase provenance: the extraction/RoB/synthesis artifacts
        # produced earlier in this same run must still be present and
        # byte-identical on disk alongside the packaged/replayed stage-5
        # artifacts -- the later package/replay round trip must not
        # disturb them, and every stage this run touched must read
        # "complete" all at once.
        assert (run_dir / "extraction.json").is_file()
        assert (run_dir / "rob.json").is_file()
        assert (run_dir / "synthesis.json").is_file()
        assert (run_dir / "extraction.json").read_bytes() == pre_package_extraction_bytes
        assert (run_dir / "rob.json").read_bytes() == pre_package_rob_bytes
        assert (run_dir / "synthesis.json").read_bytes() == pre_package_synthesis_bytes
        final_extraction = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        assert final_extraction["record_hash"] == extraction_record_hash, final_extraction

        status, record = _request(server, f"/api/runs/{run_id}")
        assert status == 200, record
        for stage in (
            "initialized",
            "doctor",
            "import",
            "dedup",
            "lock",
            "screen",
            "extraction",
            "rob",
            "synthesis",
            "package",
            "replay",
        ):
            assert record["stages"][stage] == "complete", (stage, record)
    finally:
        server.shutdown()
        thread.join()


def test_stub_package_replay_after_extraction_rob_synthesis_stays_byte_stable(tmp_path, monkeypatch):
    """docs/UI_BUILD_LOOP.md phase 10 ("Full offline stack") requires that
    `package`/`replay`, taken *after* extraction/RoB/synthesis have also
    written their own artifacts onto the same run, still bundle and
    reproduce exactly the four `PACKAGE_ARTIFACT_NAMES` artifacts
    (`frozen.json`, `dedup.json`, `lock.json`, `screen.json`) byte for
    byte, and that one record id survives every stage boundary from
    screening through extraction, RoB, and synthesis.

    `test_full_offline_stack_runs_stages_one_through_nine_and_replays`
    below already proves this same property against the real, un-stubbed
    engine CLI, but that test is skipped (not failed) whenever the
    isolated engine checkout is not present locally -- and per that test's
    own docstring, "no single earlier test exercises together" the
    cross-phase and post-extraction/RoB/synthesis package/replay
    properties it checks. That leaves this package's own always-run,
    deterministic-stub offline test suite (the one every other test in
    this file's non-skipped portion belongs to) without any coverage of
    this property at all whenever that checkout happens to be absent --
    which is exactly the situation an offline development environment
    with no engine checkout present is in. This test closes that gap: the
    same cross-phase invariant, driven entirely through the deterministic
    stub bridge (`_install_stub_bridge`) every other always-run test here
    already uses, so it is exercised unconditionally, independent of
    whether the real engine checkout happens to be available."""
    _install_stub_bridge(monkeypatch)
    server, thread = _server(tmp_path)
    try:
        run_id = _extracted_run(server, name="Full offline stack (stub)")
        run_dir = tmp_path / "runs" / run_id

        extraction_artifact = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        record_id = extraction_artifact["record_id"]
        assert record_id == "rec-1", extraction_artifact

        status, payload = _request(
            server, f"/api/runs/{run_id}/rob", "POST", _rob_body(record_id=record_id)
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload
        assert payload["rob"]["record_id"] == record_id, payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload
        assert payload["synthesis"]["status"] == "calculated", payload
        assert payload["synthesis"]["record_id"] == record_id, payload

        # Snapshot the exact on-disk bytes of the three artifacts that are
        # deliberately *outside* `PACKAGE_ARTIFACT_NAMES` (extraction/RoB/
        # synthesis) before `package`/`replay` run below. Comparing full
        # bytes -- not merely an internal hash field, and not merely file
        # existence -- is the only way to actually prove `package`/`replay`
        # do not disturb them.
        pre_package_extraction_bytes = (run_dir / "extraction.json").read_bytes()
        pre_package_rob_bytes = (run_dir / "rob.json").read_bytes()
        pre_package_synthesis_bytes = (run_dir / "synthesis.json").read_bytes()

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        manifest = payload["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload
        assert payload["replay"]["byte_stable"] is True, payload

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["packaged"] is True, comparison
        assert comparison["replayed"] is True, comparison
        assert comparison["byte_stable"] is True, comparison

        assert (run_dir / "extraction.json").read_bytes() == pre_package_extraction_bytes
        assert (run_dir / "rob.json").read_bytes() == pre_package_rob_bytes
        assert (run_dir / "synthesis.json").read_bytes() == pre_package_synthesis_bytes
        final_extraction = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        assert final_extraction["record_id"] == record_id, final_extraction

        status, record = _request(server, f"/api/runs/{run_id}")
        assert status == 200, record
        for stage in (
            "initialized",
            "import",
            "dedup",
            "lock",
            "screen",
            "extraction",
            "rob",
            "synthesis",
            "package",
            "replay",
        ):
            assert record["stages"][stage] == "complete", (stage, record)
    finally:
        server.shutdown()
        thread.join()


def test_server_module_comment_does_not_describe_rob_and_synthesize_as_future_work():
    """Regression test for a found drift, the same class of bug
    `test_static_shell_is_served` already guards against in
    `static/index.html`'s "Offline boundary" summary paragraph (see that
    test's own comment: it was found still saying "stage-8" and omitting
    stage 9 entirely, even though stage 9's own panel was already present
    and reachable), except here in `server.py`'s own leading module
    comment block rather than the frontend shell.

    `server.py`'s comment above `VALID_ACTIONS` said "It also exposes one
    source-anchored extraction action after screening; later risk-of-bias
    and synthesis stages remain separate bounded increments" -- i.e. that
    stage 8 (`rob`) and stage 9 (`synthesize`) were not yet implemented --
    even though `VALID_ACTIONS` and `ACTION_PREREQUISITES`, defined a few
    lines below that same comment in the same file, already include
    `"rob"` and `"synthesize"`. A reader of only the comment (not the code
    immediately below it) would wrongly conclude those two stages remain
    future work. This asserts the stale claim is gone and that the
    comment's own description of `server.py`'s scope actually names both
    stage 8 and stage 9, so this specific drift cannot silently reoccur."""
    server_path = Path(__file__).resolve().parent.parent / "server.py"
    source = server_path.read_text(encoding="utf-8")
    header = source[: source.index("VALID_ACTIONS")]
    assert "remain separate bounded increments" not in header, header
    assert "rob" in header.lower(), header
    assert "synthes" in header.lower(), header


def test_install_stub_bridge_docstring_does_not_undercount_covered_commands():
    """Regression test for a found drift, the same class of bug
    `test_server_module_comment_does_not_describe_rob_and_synthesize_as_future_work`
    already guards against in `server.py`'s own leading module comment,
    except here in this file's own `_install_stub_bridge` helper docstring.

    That docstring said this package "only dispatches" eight engine
    commands (`init`, `doctor`, `import`, `dedup`, `lock`, `screen`,
    `package`, `replay`) and that the stub "only needs to cover those
    eight" -- omitting `extract`, `rob`, and `synthesize` entirely, even
    though `_install_stub_bridge`'s own `fake_invoke` body (immediately
    below that docstring, in this same function) already implements
    dedicated `if command == "extract"` / `"rob"` / `"synthesize"` branches,
    and `server.py` `VALID_ACTIONS` already includes all three. A reader of
    only the docstring (not the code immediately below it) would wrongly
    conclude this stub does not cover stage 7/8/9's engine commands at all.
    This asserts the stale undercount is gone and that the docstring's own
    description of what this stub covers actually names `extract`, `rob`,
    and `synthesize`, so this specific drift cannot silently reoccur."""
    import ast

    source_path = Path(__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    docstring = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_install_stub_bridge":
            docstring = ast.get_docstring(node)
            break
    assert docstring is not None, "_install_stub_bridge must exist and have a docstring"
    assert "only needs to cover those eight" not in docstring, docstring
    assert "extract" in docstring, docstring
    assert "rob" in docstring, docstring
    assert "synthesize" in docstring, docstring


def test_no_duplicate_top_level_test_function_names_in_this_file():
    """docs/UI_BUILD_LOOP.md's Phase 10 ("Full offline stack") status
    section documents, at length, a real failure mode that already
    happened in this exact file: two top-level `def` statements sharing
    one name silently shadow each other in the module namespace pytest
    builds its test IDs from. Python raises no error for this -- the
    second assignment simply overwrites the first in the module's
    namespace -- so the earlier definition becomes dead code, never
    collected and never run, with nothing in a normal `pytest` invocation
    to signal the loss. That gap was only found and fixed by hand, more
    than once, and this document's own history shows how easy it is to
    misdiagnose (claiming a collision exists when it does not) or miss
    entirely (claiming a rename happened when it did not) without a
    mechanical check.

    This test is that mechanical check: it parses this file's own source
    with `ast` (never imports/executes it, so it cannot be fooled by
    conditional definitions or decorators the way a naive `dir(module)`
    inspection could) and asserts no two top-level `def`/`async def`
    statements in this module share a name. A future accidental collision
    -- of exactly the kind this file has already suffered from -- fails
    this test immediately, deterministically, and offline, instead of
    silently dropping coverage the way it did before."""
    import ast
    import collections

    source_path = Path(__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    top_level_def_names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    counts = collections.Counter(top_level_def_names)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    assert duplicates == [], (
        "duplicate top-level function names silently shadow one another in "
        f"pytest's collected module namespace: {duplicates}"
    )


def _top_level_test_function_names() -> set[str]:
    """Return every top-level `def`/`async def` name this test module
    actually defines, parsed via `ast` (never imported/executed) --
    the same never-import posture
    `test_no_duplicate_top_level_test_function_names_in_this_file` already
    uses. Shared by the receipt-consistency guards below so both check
    against one identical, freshly-parsed source of truth rather than two
    independently written (and potentially drifting) copies of the same
    parse."""
    import ast

    source_path = Path(__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _assert_receipt_cites_only_existing_test_functions(receipt_relative_path: str) -> None:
    """docs/UI_BUILD_LOOP.md's "Compatibility" stage (stage 6) requires "a
    versioned compatibility note" as part of its own stage-gate, and this
    document's own history (see docs/UI_BUILD_LOOP.md's "Stage 6
    ('Compatibility') status" and "Phase 10 ('Full offline stack') status"
    sections) already records at least one real instance of exactly this
    failure mode: a receipt under `docs/reviews/` asserting a specific
    `tests/test_webui.py::<name>` test existed (and had passed) before any
    function under that name existed anywhere in this file. That gap was
    only found and corrected by hand, after the fact, through direct
    inspection -- not by any mechanical check run alongside the rest of
    this offline suite, the same gap
    `test_no_duplicate_top_level_test_function_names_in_this_file` closes
    for silent name collisions within this file itself.

    This is that mechanical check for one receipt file: it extracts every
    explicit `tests/test_webui.py::<name>` citation the receipt makes (a
    narrow, unambiguous citation format -- deliberately not every
    backtick-wrapped span, most of which name JSON fields, artifact
    filenames, or other non-function concepts, not test functions) and
    asserts each named function is actually a top-level test function this
    same file defines right now. A future edit that updates a receipt's
    prose (for example naming a new pinned test, or a rename) without
    keeping it in sync with the actual test suite fails this test
    immediately and offline instead of silently drifting until the next
    manual audit finds it -- exactly the failure mode this document's own
    history shows is easy to miss."""
    import re

    receipt_path = Path(__file__).resolve().parent.parent / receipt_relative_path
    receipt_text = receipt_path.read_text(encoding="utf-8")
    cited_names = set(re.findall(r"tests/test_webui\.py::(\w+)", receipt_text))
    assert cited_names, (
        f"expected {receipt_relative_path} to cite at least one "
        "'tests/test_webui.py::<name>' test function"
    )
    missing = sorted(cited_names - _top_level_test_function_names())
    assert missing == [], (
        f"{receipt_relative_path} cites test function(s) that do not exist "
        f"anywhere in tests/test_webui.py: {missing}"
    )


def test_ui_compatibility_receipt_only_cites_test_functions_that_actually_exist():
    _assert_receipt_cites_only_existing_test_functions(
        "docs/reviews/UI_COMPATIBILITY_RECEIPT.md"
    )


def test_full_stack_offline_receipt_only_cites_test_functions_that_actually_exist():
    _assert_receipt_cites_only_existing_test_functions(
        "docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md"
    )


def _real_engine_gated_test_function_names() -> set[str]:
    """Return every top-level test function name in this module decorated
    with `@pytest.mark.skipif(not ENGINE_CHECKOUT_PRESENT, ...)`, parsed via
    `ast` (never imported/executed) -- the same never-import posture
    `_top_level_test_function_names` already uses. This is what actually
    distinguishes a genuine pinned real-engine contract check
    (docs/UI_BUILD_LOOP.md stage 6, "Compatibility": "run the UI against a
    pinned engine artifact contract") from an ordinary always-run offline
    stub test that merely happens to share a plausible-sounding name: only a
    test gated on the isolated engine checkout's presence actually drives
    the real, un-stubbed `EngineBridge` against it. A test's *existence*
    (already checked by `_assert_receipt_cites_only_existing_test_functions`)
    is not itself proof it is the specific kind of test stage 6's own
    versioned compatibility note requires."""
    import ast

    source_path = Path(__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    gated: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if "ENGINE_CHECKOUT_PRESENT" in ast.dump(decorator):
                gated.add(node.name)
                break
    return gated


def _assert_receipt_cites_only_real_engine_gated_tests(receipt_relative_path: str) -> None:
    """A narrower, additional mechanical guard layered on top of
    `_assert_receipt_cites_only_existing_test_functions`: it is not enough
    for a `tests/test_webui.py::<name>` citation in a stage-6/phase-10
    receipt to name a function that merely *exists* -- a future edit could
    cite an existing but wrongly-scoped test (for example an always-run
    offline stub test with a similarly real-engine-sounding name) and that
    narrower existence check alone would not catch it, even though the
    receipt would then be certifying a pinned real-engine contract check
    that never actually touched the isolated engine checkout at all. This
    asserts every explicit `tests/test_webui.py::<name>` citation the
    receipt makes names a test that is actually gated on
    `ENGINE_CHECKOUT_PRESENT` (see `_real_engine_gated_test_function_names`),
    so that drift fails immediately and offline instead of only being
    caught by a manual audit -- mirroring this document's own repeated
    posture that a claim's presence is not proof of what it verifies."""
    import re

    receipt_path = Path(__file__).resolve().parent.parent / receipt_relative_path
    receipt_text = receipt_path.read_text(encoding="utf-8")
    cited_names = set(re.findall(r"tests/test_webui\.py::(\w+)", receipt_text))
    assert cited_names, (
        f"expected {receipt_relative_path} to cite at least one "
        "'tests/test_webui.py::<name>' test function"
    )
    not_gated = sorted(cited_names - _real_engine_gated_test_function_names())
    assert not_gated == [], (
        f"{receipt_relative_path} cites test function(s) that are not gated on the "
        "isolated engine checkout's presence (ENGINE_CHECKOUT_PRESENT), so they "
        f"cannot be the pinned real-engine contract check the receipt claims: {not_gated}"
    )


def test_ui_compatibility_receipt_only_cites_real_engine_gated_tests():
    _assert_receipt_cites_only_real_engine_gated_tests(
        "docs/reviews/UI_COMPATIBILITY_RECEIPT.md"
    )


def test_full_stack_offline_receipt_only_cites_real_engine_gated_tests():
    _assert_receipt_cites_only_real_engine_gated_tests(
        "docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md"
    )


def _check_receipt_verified_marker_matches_status(
    receipt_text: str, marker_name: str, *, receipt_relative_path: str = "<receipt>"
) -> None:
    """The actual, path-independent logic behind
    `_assert_receipt_verified_marker_matches_status` below, factored out so
    it can be exercised directly against synthetic receipt text (see
    `test_check_receipt_verified_marker_matches_status_rejects_verified_status_without_marker_line`
    and its accompanying positive-path test) without needing a real file on
    disk under `docs/reviews/` -- this file's tests must never write to, or
    depend on the current content of, either real receipt to prove this
    check's own logic is correct in both directions.

    A further, narrow gap remained even with both existing
    receipt-citation guard families above (before this function itself was
    split out of `_assert_receipt_verified_marker_matches_status`): neither
    checks that a receipt's own `STATUS: ...` line and its standalone
    `<marker_name>: true` marker line (the exact marker line the task's own
    contract requires, e.g. `FULL_STACK_OFFLINE_VERIFIED: true`) actually
    agree with one another. docs/UI_BUILD_LOOP.md's Stage 6 and Phase 10
    status sections already record a real instance of exactly this failure
    mode happening by hand: both receipts previously asserted their
    `..._VERIFIED: true` marker while the pinned real-engine test the
    marker depends on did not yet exist anywhere in this file, and both
    assertions had to be withdrawn after the fact through direct
    inspection, not caught mechanically.

    This is that mechanical check: it finds the receipt's own `STATUS: ...`
    line (a real, standalone line at the very start of a physical line) and
    checks for a standalone `<marker_name>: true` line, anchored to an
    entire line so explanatory prose that merely *mentions* the marker
    inline -- such as both receipts' own "previously asserted
    `..._VERIFIED: true`" withdrawal narrative -- is never mistaken for a
    live assertion. The three directions this now enforces: if the marker
    line is present, `STATUS:` must start with `verified`; if `STATUS:`
    starts with `pending`, the marker line must not be present; and if
    `STATUS:` starts with `verified`, the marker line *must* be present.
    That third direction was the one genuine remaining gap: the two checks
    originally here covered marker-present -> status-verified and
    status-pending -> marker-absent, but never status-verified ->
    marker-present, so a future edit that promoted a receipt's `STATUS:`
    line to `verified` without ever adding the required marker line would
    have silently passed this guard, even though this function's own
    docstring already claimed the two "actually agree with one another." A
    future edit that adds the marker line without promoting `STATUS:`, that
    promotes `STATUS:` without adding the marker line, or that leaves
    `STATUS:` reading `pending` while still asserting the marker line, now
    fails this check immediately and offline instead of silently drifting
    until the next manual audit finds it."""
    import re

    status_match = re.search(r"^STATUS: (.+)$", receipt_text, re.MULTILINE)
    assert status_match is not None, f"{receipt_relative_path} has no top-level STATUS: line"
    status_value = status_match.group(1).strip()

    marker_present = (
        re.search(rf"^{re.escape(marker_name)}: true$", receipt_text, re.MULTILINE)
        is not None
    )

    if marker_present:
        assert status_value.startswith("verified"), (
            f"{receipt_relative_path} asserts a standalone `{marker_name}: true` "
            f"line but its STATUS line reads {status_value!r}, not 'verified'"
        )
    if status_value.startswith("pending"):
        assert not marker_present, (
            f"{receipt_relative_path} STATUS line reads 'pending' but also asserts "
            f"a standalone `{marker_name}: true` marker line"
        )
    if status_value.startswith("verified"):
        assert marker_present, (
            f"{receipt_relative_path} STATUS line reads {status_value!r} but does not "
            f"assert a standalone `{marker_name}: true` marker line"
        )


def _assert_receipt_verified_marker_matches_status(
    receipt_relative_path: str, marker_name: str
) -> None:
    receipt_path = Path(__file__).resolve().parent.parent / receipt_relative_path
    receipt_text = receipt_path.read_text(encoding="utf-8")
    _check_receipt_verified_marker_matches_status(
        receipt_text, marker_name, receipt_relative_path=receipt_relative_path
    )


def test_check_receipt_verified_marker_matches_status_rejects_verified_status_without_marker_line():
    """Regression test for the gap
    `_check_receipt_verified_marker_matches_status` closed this session:
    previously, the guard only asserted marker-present implies
    `STATUS: verified...` and `STATUS: pending` implies marker-absent, but
    never the reverse -- that a `STATUS: verified...` line actually requires
    the `..._VERIFIED: true` marker line to be present. This constructs
    exactly that malformed receipt shape as plain text (never touching
    either real file under docs/reviews/) and asserts the check now raises
    against it."""
    with pytest.raises(AssertionError):
        _check_receipt_verified_marker_matches_status(
            "# Fake receipt\n\nSTATUS: verified -- genuinely passed.\n",
            "FAKE_VERIFIED",
        )


def test_check_receipt_verified_marker_matches_status_accepts_verified_status_with_marker_line():
    """The positive-path complement to the regression test above: a
    receipt whose `STATUS:` line reads `verified...` and that also asserts
    the matching standalone `<marker_name>: true` line must still pass this
    check -- the new assertion added this session must not reject a
    genuinely well-formed verified receipt, only one missing the marker
    line entirely."""
    _check_receipt_verified_marker_matches_status(
        "# Fake receipt\n\nSTATUS: verified -- genuinely passed.\n\nFAKE_VERIFIED: true\n",
        "FAKE_VERIFIED",
    )


def test_ui_compatibility_receipt_verified_marker_matches_status():
    _assert_receipt_verified_marker_matches_status(
        "docs/reviews/UI_COMPATIBILITY_RECEIPT.md", "UI_COMPATIBILITY_VERIFIED"
    )


def test_full_stack_offline_receipt_verified_marker_matches_status():
    _assert_receipt_verified_marker_matches_status(
        "docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md", "FULL_STACK_OFFLINE_VERIFIED"
    )


def test_ui_compatibility_receipt_engine_checkout_path_matches_test_constant():
    """A further, narrow gap in stage 6's own "versioned compatibility note"
    requirement: none of the existing receipt-citation/scope/marker guards
    above check that a receipt's own factual claim about *which* isolated
    engine checkout the pinned real-engine test runs against actually
    matches the path this test module itself uses to gate and invoke that
    test (`ENGINE_ROOT`, used to compute `ENGINE_CHECKOUT_PRESENT` and to
    construct `create_server(engine_root=ENGINE_ROOT, ...)` for every
    real-engine test in this file, including
    `test_stage_six_full_pipeline_runs_against_real_engine_cli`). A future
    edit that moved, renamed, or repointed `ENGINE_ROOT` without updating
    `docs/reviews/UI_COMPATIBILITY_RECEIPT.md`'s own "Engine checkout: ..."
    line (or the reverse) would leave the receipt's own stated scope
    silently inaccurate -- exactly the same class of drift this document's
    other citation/scope/marker guards already catch for *which test* is
    cited, but not yet for *which engine checkout* that test is claimed to
    run against. This is that mechanical check: it extracts the receipt's
    standalone "- Engine checkout: `<path>`" line and asserts it names the
    exact same path as this module's own `ENGINE_ROOT` constant."""
    import re

    receipt_path = (
        Path(__file__).resolve().parent.parent / "docs/reviews/UI_COMPATIBILITY_RECEIPT.md"
    )
    receipt_text = receipt_path.read_text(encoding="utf-8")
    match = re.search(r"^- Engine checkout: `(.+)`$", receipt_text, re.MULTILINE)
    assert match is not None, (
        "docs/reviews/UI_COMPATIBILITY_RECEIPT.md has no standalone "
        "'- Engine checkout: `<path>`' line"
    )
    assert match.group(1) == str(ENGINE_ROOT), (
        "docs/reviews/UI_COMPATIBILITY_RECEIPT.md's cited engine checkout path "
        f"{match.group(1)!r} does not match this test module's own ENGINE_ROOT "
        f"{str(ENGINE_ROOT)!r}"
    )


def test_ui_build_loop_stages_section_lists_expected_stage_names_in_order():
    """docs/UI_BUILD_LOOP.md's own "## Stages" section is the single source
    of truth this repository's controller cites `STAGE: <name>` from (see
    that section's six numbered, bold-named entries: "Operator shell",
    "Frozen input", "Deduplication and lock", "Screening", "Replay and
    audit", "Compatibility") -- and every mechanical guard already in this
    file that protects a receipt's citation accuracy, a receipt's marker
    consistency, or a receipt's cited engine-checkout path (see
    `_assert_receipt_cites_only_existing_test_functions`,
    `_assert_receipt_cites_only_real_engine_gated_tests`,
    `_assert_receipt_verified_marker_matches_status`, and
    `test_ui_compatibility_receipt_engine_checkout_path_matches_test_constant`
    above) exists precisely because this document's own history (see
    docs/UI_BUILD_LOOP.md's lengthy "Stage 6" and "Phase 10" status
    sections) shows this kind of drift happens by hand and is easy to miss
    without a mechanical check.

    Nothing previously checked the "## Stages" section itself: a future
    edit that renamed, reordered, duplicated, or dropped one of these six
    stage names (for example while editing the surrounding prose, which
    this document's own status sections show has already drifted more than
    once in other ways) would silently break the one place downstream
    tooling and reviewers are told to read the canonical stage name from,
    with nothing in this offline suite to catch it. This is that
    mechanical check: it extracts the "## Stages" section's text (up to
    the next `##` heading) and asserts its six `**...**`-bolded stage
    names appear, in order, exactly as this document currently defines
    them."""
    import re

    doc_path = Path(__file__).resolve().parent.parent / "docs" / "UI_BUILD_LOOP.md"
    doc_text = doc_path.read_text(encoding="utf-8")

    section_start = doc_text.index("## Stages")
    section_end = doc_text.index("\n## ", section_start + len("## Stages"))
    stages_section = doc_text[section_start:section_end]

    stage_names = re.findall(r"\*\*(.+?)\*\*", stages_section)
    assert stage_names == [
        "Operator shell",
        "Frozen input",
        "Deduplication and lock",
        "Screening",
        "Replay and audit",
        "Compatibility",
    ], stage_names


def test_ui_build_loop_next_offline_phases_section_lists_expected_phase_name():
    """The same class of drift-protection
    `test_ui_build_loop_stages_section_lists_expected_stage_names_in_order`
    already applies to docs/UI_BUILD_LOOP.md's "## Stages" section had never
    been applied to that document's other canonical-name section: "## Next
    offline phases", whose own single `10. **Full offline stack** — ...`
    bold-named entry is the exact phase name this repository's own task
    instructions (and `docs/reviews/FULL_STACK_OFFLINE_RECEIPT.md`) depend on
    downstream readers finding unambiguously. Nothing previously checked that
    section mechanically -- a future edit that renamed, reworded, or dropped
    that bold name while editing the surrounding prose (the exact class of
    drift this document's own "Stage 6"/"Phase 10 status" sections already
    record happening more than once elsewhere) would silently break the one
    place downstream tooling and reviewers are told to read that phase's
    canonical name from, with nothing in this offline suite to catch it.

    This is that mechanical check, for that section, mirroring
    `test_ui_build_loop_stages_section_lists_expected_stage_names_in_order`'s
    own logic exactly: it extracts the "## Next offline phases" section's
    text (up to the next `##` heading) and asserts its one `**...**`-bolded
    phase name still reads, verbatim, "Full offline stack"."""
    import re

    doc_path = Path(__file__).resolve().parent.parent / "docs" / "UI_BUILD_LOOP.md"
    doc_text = doc_path.read_text(encoding="utf-8")

    section_start = doc_text.index("## Next offline phases")
    section_end = doc_text.index("\n## ", section_start + len("## Next offline phases"))
    phases_section = doc_text[section_start:section_end]

    phase_names = re.findall(r"\*\*(.+?)\*\*", phases_section)
    assert phase_names == ["Full offline stack"], phase_names


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason=f"isolated engine checkout not found at {ENGINE_ROOT}",
)
def test_stage_one_operator_shell_runs_against_real_engine_cli(tmp_path):
    """Drive this package's actual stage-1 scope -- run creation (`init`)
    and the offline `doctor` diagnostic -- through the real, un-stubbed
    EngineBridge (the same `screen_prove.cli` invocations server.py issues
    in production) against the isolated offline engine checkout. Unlike the
    stubbed tests above -- which only prove the WebUI's own HTTP logic --
    this exercises the actual `init`/`doctor` command contract, so a real
    incompatibility between this bridge and the versioned CLI (a renamed
    flag, changed exit code, changed output path, etc.) fails here instead
    of going undetected. No stub, no network access, and no credentials are
    used: the CLI is invoked exactly as EngineBridge.run always invokes it,
    through the same allowlisted, HOME-redirected subprocess environment
    (see bridge.py and the credential-isolation tests above). This
    deliberately does not exercise import/dedup/lock/screen/package/replay:
    those commands are out of scope for this stage-1 package (see
    docs/UI_BUILD_LOOP.md) and are not reachable through server.py.

    The `doctor` assertions below deliberately do not require
    `payload["doctor"]["passed"]` to be True: the real engine's readiness
    checks can legitimately report warnings against an ordinary local
    checkout (for example unrelated untracked planning files), and that is
    not a WebUI failure -- it is exactly the diagnostic information stage 1
    is supposed to surface to the operator (see
    test_doctor_warnings_are_reported_not_swallowed_as_a_bridge_error for
    the stubbed version of this same contract). What must hold regardless
    of whether the engine's checks passed is: the HTTP action itself
    succeeds, the `doctor` stage is recorded complete, and the diagnostic's
    own result (passed, exit code, stderr, full report) is returned intact
    for the operator to see."""
    server, thread = _server(tmp_path)
    try:
        status, run = _request(server, "/api/runs", "POST", {"name": "Real engine run"})
        assert status == 201, run
        assert run.get("last_error") is None, run
        run_id = run["id"]

        run_dir = tmp_path / "runs" / run_id
        for filename in ("protocol.json", "procedure.json", "schema.json"):
            assert (run_dir / filename).is_file(), filename

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete", payload
        assert payload["run"]["last_error"] is None, payload

        doctor = payload["doctor"]
        assert isinstance(doctor["passed"], bool), doctor
        assert isinstance(doctor["exit_code"], int), doctor
        assert isinstance(doctor["stderr"], str), doctor
        assert isinstance(doctor["report"], dict), doctor
        assert doctor["report"].get("passed") == doctor["passed"], doctor

        artifact_names = {item["name"] for item in payload["artifacts"]}
        assert "doctor.json" in artifact_names, artifact_names

        status, health = _request(server, "/api/health")
        assert status == 200
        assert health["ok"] is True
        assert health["credentials_loaded"] is False
        assert health["credentials_isolated"] is True
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason=f"isolated engine checkout not found at {ENGINE_ROOT}",
)
def test_stage_six_full_pipeline_runs_against_real_engine_cli_with_artifact_and_replay_comparison_checks(
    tmp_path,
):
    """docs/UI_BUILD_LOOP.md stage 6 ("Compatibility"): drive this
    package's *entire* scope -- run creation, `import`, `dedup`, `lock`,
    `screen` (including, if the cached screening result abstains on this
    run's only record, `adjudicate`), `package`, and `replay` -- through
    the real, un-stubbed `EngineBridge` against the isolated offline
    engine checkout, exactly as `server.py` invokes it in production.

    Rename note: this function previously shared the top-level name
    `test_stage_six_full_pipeline_runs_against_real_engine_cli` with the
    earlier, differently-scoped definition above (added while stage 6 was
    first implemented). That collision is exactly the silent-shadowing
    failure mode `test_no_duplicate_top_level_test_function_names_in_this_file`
    (added later, during phase 10) exists to catch: the second top-level
    `def` in this module simply overwrote the first in pytest's collected
    namespace, so the earlier, simpler stage-6 real-engine test was never
    actually collected or run, with nothing in an ordinary `pytest`
    invocation to signal the loss. It was found by direct inspection (not
    by running the guard test, which this session could not execute) and
    fixed by renaming this later definition -- the one that additionally
    checks post-import artifact names, the read-only GET replay
    comparison route, and the artifact-content inspection route -- to a
    distinct name, so both stage-6 real-engine tests are now actually
    collected. Neither test's assertions were weakened; only the name
    collision was fixed.

    Every earlier stage's own tests only prove this WebUI's HTTP and
    stage-bookkeeping logic against a deterministic stand-in (see
    `_install_stub_bridge`); `bridge.py`'s `import_records`/
    `dedup_records`/`lock_protocol`/`screen_records`/`package_run`/
    `replay_run` docstrings, and docs/UI_BUILD_LOOP.md's "Current package
    status", are explicit that this real-engine compatibility check is
    this dedicated stage's job, not an earlier stage's. This test is that
    pinned engine-artifact contract check: the exact CLI shape (positional
    argument, `--source`/`--output`/`--raw-dir`/`--dedup`/`--output-dir`
    flag names) each bridge method already assumes, and the artifact
    shape (`frozen.json`, `dedup.json`, `lock.json`, `screen.json`,
    `package.json`, and byte-identical `replay/*` output) `server.py`
    already requires from a well-behaved engine, is exercised against
    the actual, currently-checked-out `screen_prove.cli` -- not merely
    against a fixture written to satisfy this package's own assumptions.

    No stub, no network access, and no credentials are used -- only the
    real, local, offline engine CLI invoked exactly as `EngineBridge`
    always invokes it, through the same allowlisted, HOME-redirected
    subprocess environment already covered by
    `test_stage_one_operator_shell_runs_against_real_engine_cli`. This
    test is skipped (not failed) when that checkout is not present
    locally, matching that same existing pattern; this package's own
    offline test suite (every deterministic-stub test above) never
    depends on it, and this test itself never reads or modifies anything
    inside that checkout -- it only invokes its CLI as a subprocess, the
    same way this WebUI always does in production.
    """
    server, thread = _server(tmp_path)
    try:
        status, run = _request(server, "/api/runs", "POST", {"name": "Stage six pipeline"})
        assert status == 201, run
        run_id = run["id"]

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {
                "filename": "stage-six.ris",
                "content_base64": _b64(
                    b"TY  - JOUR\r\n"
                    b"TI  - A minimal record for offline compatibility verification\r\n"
                    b"AU  - Doe, Jane\r\n"
                    b"PY  - 2024\r\n"
                    b"ER  -  \r\n"
                ),
            },
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload
        artifact_names = {item["name"] for item in payload["artifacts"]}
        assert "source/stage-six.ris" in artifact_names, payload
        assert "frozen.json" in artifact_names, payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload

        # Turn human adjudication on *before* screening so that, if the
        # real engine's cached screening result genuinely abstains on this
        # run's only locked record, the "blocked" branch below is actually
        # reachable and this test genuinely exercises `adjudicate` against
        # the real engine too -- not merely against the stub the rest of
        # this suite already covers.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        screen_status = payload["run"]["stages"]["screen"]
        assert screen_status in ("complete", "blocked"), payload

        if screen_status == "blocked":
            # A cached screening result that abstains on this run's only
            # locked record is a legitimate real-engine outcome, not a
            # WebUI failure (docs/UI_BUILD_LOOP.md stage 4's "abstention
            # and blocking queue" contract) -- resolve it with an ordinary
            # human decision so this test can still reach `package`/
            # `replay`, the actual scope this stage exists to verify.
            for record_id in payload["screen"]["remaining"]:
                status, payload = _request(
                    server,
                    f"/api/runs/{run_id}/adjudicate",
                    "POST",
                    {"id": record_id, "decision": "include"},
                )
                assert status == 200, payload
            assert payload["run"]["stages"]["screen"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        manifest = payload["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload
        assert payload["replay"]["byte_stable"] is True, payload

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["packaged"] is True, comparison
        assert comparison["replayed"] is True, comparison
        assert comparison["byte_stable"] is True, comparison

        status, artifact = _request(server, f"/api/runs/{run_id}/artifacts/screen.json")
        assert status == 200, artifact
        assert artifact["sha256"], artifact
        assert artifact["content_base64"], artifact
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason=f"isolated engine checkout not found at {ENGINE_ROOT}",
)
def test_stage_seven_extraction_runs_against_real_engine_cli(tmp_path):
    """Stage 7 compatibility: drive the real engine's source-anchored
    `extract` command through the WebUI bridge after a real offline screen.

    This keeps full-text retrieval and live connectors out of scope: the
    source is an explicitly supplied local UTF-8 fixture, and the engine
    must produce the verified `NR`/reported/`ESTIMATED` artifact the WebUI
    contract checks before marking extraction complete.
    """
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server, name="Stage seven extraction")
        source = b"Sample size was 240 participants.\n"
        status, toggle = _request(
            server,
            f"/api/runs/{run_id}/human_adjudication",
            "POST",
            {"enabled": True},
        )
        assert status == 200, toggle
        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        for record_id in screening.get("remaining", []):
            status, adjudication = _request(
                server,
                f"/api/runs/{run_id}/adjudicate",
                "POST",
                {"id": record_id, "decision": "include"},
            )
            assert status == 200, adjudication
        screen = json.loads((tmp_path / "runs" / run_id / "screen.json").read_text(encoding="utf-8"))
        decisions = screen.get("decisions") or [
            {"id": item["record_id"], "decision": item["decision"]}
            for item in screen.get("results", [])
        ]
        record_id = next(
            (item["id"] for item in decisions if item["decision"] == "include"),
            decisions[0]["id"],
        )
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete", payload
        assert payload["extraction"]["field_counts"]["reported"] == 1, payload
        assert payload["extraction"]["field_counts"]["not_reported"] == 1, payload
        assert payload["extraction"]["field_counts"]["estimated"] == 1, payload
        run_dir = tmp_path / "runs" / run_id
        assert (run_dir / "extraction.json").is_file()
        assert (run_dir / "extraction" / "source" / "study-fulltext.txt").read_bytes() == source
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason=f"isolated engine checkout not found at {ENGINE_ROOT}",
)
def test_stage_eight_rob_runs_against_real_engine_cli(tmp_path):
    """Stage 8 compatibility: drive the real engine's bounded `rob`
    command after a real offline screen and source-anchored extraction.

    Any cached abstentions are resolved locally through the explicit human
    toggle and queue; no model or live evidence source is contacted.
    """
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server, name="Stage eight risk of bias")
        status, toggle = _request(
            server,
            f"/api/runs/{run_id}/human_adjudication",
            "POST",
            {"enabled": True},
        )
        assert status == 200, toggle
        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        for record_id in screening.get("remaining", []):
            status, adjudication = _request(
                server,
                f"/api/runs/{run_id}/adjudicate",
                "POST",
                {"id": record_id, "decision": "include"},
            )
            assert status == 200, adjudication
        screen = json.loads((tmp_path / "runs" / run_id / "screen.json").read_text(encoding="utf-8"))
        decisions = screen.get("decisions") or [
            {"id": item["record_id"], "decision": item["decision"]}
            for item in screen.get("results", [])
        ]
        record_id = next(
            (item["id"] for item in decisions if item["decision"] == "include"),
            decisions[0]["id"],
        )
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id=record_id),
        )
        assert status == 200, payload
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/rob",
            "POST",
            _rob_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload
        assert payload["rob"]["domain_count"] == 2, payload
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason=f"isolated engine checkout not found at {ENGINE_ROOT}",
)
def test_stage_nine_synthesis_runs_against_real_engine_cli(tmp_path):
    """Stage 9 compatibility: drive the real single-study `synthesize`
    command after real offline screening, extraction, and RoB artifacts."""
    server, thread = _server(tmp_path)
    try:
        run_id = _screened_run(server, name="Stage nine synthesis")
        status, toggle = _request(
            server,
            f"/api/runs/{run_id}/human_adjudication",
            "POST",
            {"enabled": True},
        )
        assert status == 200, toggle
        status, screening = _request(server, f"/api/runs/{run_id}/screening")
        assert status == 200, screening
        for record_id in screening.get("remaining", []):
            status, adjudication = _request(
                server,
                f"/api/runs/{run_id}/adjudicate",
                "POST",
                {"id": record_id, "decision": "include"},
            )
            assert status == 200, adjudication
        screen = json.loads((tmp_path / "runs" / run_id / "screen.json").read_text(encoding="utf-8"))
        decisions = screen.get("decisions") or [
            {"id": item["record_id"], "decision": item["decision"]}
            for item in screen.get("results", [])
        ]
        record_id = next(
            (item["id"] for item in decisions if item["decision"] == "include"),
            decisions[0]["id"],
        )
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id=record_id),
        )
        assert status == 200, payload
        status, payload = _request(
            server, f"/api/runs/{run_id}/rob", "POST", _rob_body(record_id=record_id)
        )
        assert status == 200, payload
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload
        assert payload["synthesis"]["status"] == "calculated", payload
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.skipif(
    not ENGINE_CHECKOUT_PRESENT,
    reason=f"isolated engine checkout not found at {ENGINE_ROOT}",
)
def test_full_offline_stack_runs_stages_one_through_nine_and_replays(tmp_path):
    """docs/UI_BUILD_LOOP.md phase 10 ("Full offline stack"): drive stages
    1-9 through *one* local run against the real, un-stubbed `EngineBridge`
    -- run creation/doctor, `import`, `dedup`, `lock`, `screen` (resolving
    via `adjudicate` if the real engine's cached screening result happens
    to abstain), source-anchored `extract`, bounded `rob`, single-study
    `synthesize`, `package`, and `replay` -- and verify both cross-phase
    provenance (the same included record id is carried, unmodified, from
    screening through extraction, RoB, and synthesis) and that stage 5's
    package/replay contract still holds, byte-stable, *after* those later
    local stages have also written their own artifacts on top of the same
    run directory.

    Every action driven here is already covered individually against the
    real engine CLI by `test_stage_one_operator_shell_runs_against_real_engine_cli`,
    `test_stage_six_full_pipeline_runs_against_real_engine_cli`, and
    `test_stage_{seven,eight,nine}_..._runs_against_real_engine_cli`. What
    only a single continuous run can prove is that `extract`/`rob`/
    `synthesize` writing their own artifacts onto an already-locked/
    screened run does not disturb `package`'s four
    frozen/dedup/lock/screen artifacts (docs/UI_BUILD_LOOP.md stage 5's
    `PACKAGE_ARTIFACT_NAMES` contract deliberately excludes extraction/RoB/
    synthesis), that a `package`/`replay` taken *after* those later stages
    still reproduces exactly those four artifacts byte for byte, and that
    one record id genuinely survives every stage boundary in between.

    No stub, no network access, and no credentials are used -- only the
    real, local, offline `screen_prove.cli` invoked as a subprocess the
    same way this WebUI always invokes it in production. Skipped (not
    failed) when the isolated engine checkout is not present locally,
    matching every other real-engine test in this file; this package's
    own offline test suite (every deterministic-stub test above) does not
    depend on it.
    """
    server, thread = _server(tmp_path)
    try:
        status, run = _request(
            server, "/api/runs", "POST", {"name": "Full offline stack"}
        )
        assert status == 201, run
        run_id = run["id"]

        status, payload = _request(server, f"/api/runs/{run_id}/doctor", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["doctor"] == "complete", payload

        status, payload = _request(
            server,
            f"/api/runs/{run_id}/import",
            "POST",
            {
                "filename": "full-stack.ris",
                "content_base64": _b64(
                    b"TY  - JOUR\r\n"
                    b"TI  - A minimal record for full offline stack verification\r\n"
                    b"AU  - Doe, Jane\r\n"
                    b"PY  - 2024\r\n"
                    b"ER  -  \r\n"
                ),
            },
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["import"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/dedup", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["dedup"] == "complete", payload

        status, payload = _request(server, f"/api/runs/{run_id}/lock", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["lock"] == "complete", payload

        # Turn human adjudication on *before* screening so that, if the
        # real engine's cached screening result genuinely abstains on this
        # run's only locked record, the "blocked" branch below is actually
        # reachable and this run genuinely exercises `adjudicate` too.
        status, payload = _request(
            server, f"/api/runs/{run_id}/human_adjudication", "POST", {"enabled": True}
        )
        assert status == 200, payload
        assert payload["run"]["human_adjudication"] == "on", payload

        status, payload = _request(server, f"/api/runs/{run_id}/screen", "POST", {})
        assert status == 200, payload
        screen_status = payload["run"]["stages"]["screen"]
        assert screen_status in ("complete", "blocked"), payload

        if screen_status == "blocked":
            for record_id in payload["screen"]["remaining"]:
                status, payload = _request(
                    server,
                    f"/api/runs/{run_id}/adjudicate",
                    "POST",
                    {"id": record_id, "decision": "include"},
                )
                assert status == 200, payload
            assert payload["run"]["stages"]["screen"] == "complete", payload

        run_dir = tmp_path / "runs" / run_id
        screen_artifact = json.loads((run_dir / "screen.json").read_text(encoding="utf-8"))
        decisions = screen_artifact.get("decisions") or [
            {"id": item["record_id"], "decision": item["decision"]}
            for item in screen_artifact.get("results", [])
        ]

        # Resolve each record's *effective* decision: a human `adjudicate`
        # call (exercised above whenever the real engine's cached screening
        # abstained) overrides that record's raw engine decision, exactly as
        # `_screening_state()` (server.py) resolves the blocking queue. Using
        # the raw `screen.json` decision alone here would let an abstained
        # record that was actually adjudicated `include` be misread as still
        # unresolved, and -- the actual bug this guards against -- would let
        # an all-`exclude` raw screening result silently fall back to
        # selecting an excluded record below, which would prove nothing
        # about provenance from an *included* record.
        adjudications_path = run_dir / "adjudications.json"
        adjudications = (
            json.loads(adjudications_path.read_text(encoding="utf-8"))
            if adjudications_path.is_file()
            else {}
        )
        effective_decisions = {
            item["id"]: adjudications.get(item["id"], item["decision"])
            for item in decisions
        }
        included_ids = [
            record_id
            for record_id, decision in effective_decisions.items()
            if decision == "include"
        ]
        assert included_ids, (
            "no record resolved to an effective 'include' decision; "
            "cross-phase provenance from an included record cannot be "
            f"established: {effective_decisions}"
        )
        record_id = included_ids[0]

        # --- Stage 7: source-anchored extraction -------------------------
        source = b"Sample size was 240 participants.\n"
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/extract",
            "POST",
            _extraction_body(source, record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["extraction"] == "complete", payload
        extraction_record_hash = payload["extraction"]["record_hash"]
        extraction_artifact = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        assert extraction_artifact["record_id"] == record_id, extraction_artifact

        # --- Stage 8: bounded risk-of-bias --------------------------------
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/rob",
            "POST",
            _rob_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["rob"] == "complete", payload
        assert payload["rob"]["record_id"] == record_id, payload

        # --- Stage 9: bounded single-study synthesis ----------------------
        status, payload = _request(
            server,
            f"/api/runs/{run_id}/synthesize",
            "POST",
            _synthesis_body(record_id=record_id),
        )
        assert status == 200, payload
        assert payload["run"]["stages"]["synthesis"] == "complete", payload
        assert payload["synthesis"]["status"] == "calculated", payload
        assert payload["synthesis"]["record_id"] == record_id, payload

        # Snapshot the exact on-disk bytes of the three artifacts that are
        # deliberately *outside* `PACKAGE_ARTIFACT_NAMES` (extraction/RoB/
        # synthesis) before `package`/`replay` run below. Comparing full
        # bytes -- not merely re-checking a hash field embedded inside the
        # JSON itself, and not merely re-checking the files still exist --
        # is the only way to actually prove `package`/`replay` do not
        # disturb them: an internal `record_hash` field only proves the
        # artifact's own recorded content is internally self-consistent,
        # never that `package`/`replay` left the file itself untouched.
        pre_package_extraction_bytes = (run_dir / "extraction.json").read_bytes()
        pre_package_rob_bytes = (run_dir / "rob.json").read_bytes()
        pre_package_synthesis_bytes = (run_dir / "synthesis.json").read_bytes()

        # --- Stage 5: package/replay, taken *after* extraction/RoB/
        # synthesis have also written their own artifacts onto this run --
        # must still bundle and reproduce exactly the four
        # frozen/dedup/lock/screen artifacts, byte for byte.
        status, payload = _request(server, f"/api/runs/{run_id}/package", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["package"] == "complete", payload
        manifest = payload["package"]["manifest"]
        assert set(manifest) == set(PACKAGE_ARTIFACT_NAMES), payload

        status, payload = _request(server, f"/api/runs/{run_id}/replay", "POST", {})
        assert status == 200, payload
        assert payload["run"]["stages"]["replay"] == "complete", payload
        assert payload["replay"]["byte_stable"] is True, payload

        status, comparison = _request(server, f"/api/runs/{run_id}/replay")
        assert status == 200, comparison
        assert comparison["packaged"] is True, comparison
        assert comparison["replayed"] is True, comparison
        assert comparison["byte_stable"] is True, comparison

        # Cross-phase provenance: the extraction/RoB/synthesis artifacts
        # produced earlier in this same run must still be present and
        # byte-identical on disk alongside the packaged/replayed stage-5
        # artifacts -- the later package/replay round trip must not
        # disturb them, and every stage this run touched must read
        # "complete" all at once. Compare the exact bytes captured above
        # (not merely file existence, and not merely an internal hash
        # field) against what is on disk now, for all three artifacts.
        assert (run_dir / "extraction.json").is_file()
        assert (run_dir / "rob.json").is_file()
        assert (run_dir / "synthesis.json").is_file()
        assert (run_dir / "extraction.json").read_bytes() == pre_package_extraction_bytes
        assert (run_dir / "rob.json").read_bytes() == pre_package_rob_bytes
        assert (run_dir / "synthesis.json").read_bytes() == pre_package_synthesis_bytes
        final_extraction = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
        assert final_extraction["record_hash"] == extraction_record_hash, final_extraction

        status, record = _request(server, f"/api/runs/{run_id}")
        assert status == 200, record
        for stage in (
            "initialized",
            "doctor",
            "import",
            "dedup",
            "lock",
            "screen",
            "extraction",
            "rob",
            "synthesis",
            "package",
            "replay",
        ):
            assert record["stages"][stage] == "complete", (stage, record)
    finally:
        server.shutdown()
        thread.join()


def test_validate_extraction_artifact_reports_hash_mismatch_not_nan_crash(tmp_path):
    """`_validate_extraction_artifact` (server.py) has a documented guard
    around its own `record_hash` check: `_object_hash` serializes the
    artifact body with `allow_nan=False` (the engine's own canonical hash
    contract), so a body containing a non-finite float (NaN/Infinity)
    *anywhere* -- including in a field this function never otherwise
    inspects -- must raise `BridgeError("extraction artifact record_hash
    does not match its content")`, not let the underlying
    `TypeError`/`ValueError` from `json.dumps` escape past this function's
    own documented "fails closed (raises BridgeError)" contract.

    Before this test, that guard had no coverage anywhere in this package's
    always-run offline suite: `_install_stub_bridge`'s `extract_output_mutation`
    hook mutates the body *before* the stub itself calls this same
    `_object_hash` helper to compute `record_hash` (see the `command ==
    "extract"` branch above), so a mutation that introduces a NaN/Infinity
    value makes the *stub* raise while constructing its own fixture, long
    before the server-side artifact validation this test targets ever runs.
    Reaching the guard requires writing a malformed artifact file directly
    (bypassing the stub) so a literal NaN reaches `_validate_extraction_artifact`
    itself. A regression that removed this function's `try`/`except
    (TypeError, ValueError)` guard would let such a malformed artifact raise
    an unhandled `TypeError`/`ValueError` (a 500) instead of the documented
    `BridgeError` (a clean, reported failure) -- and nothing in this file
    would have caught it before this test."""
    source_bytes = b"Sample size was 240 participants.\n"
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    body = {
        "version": "extraction-record-v1",
        "record_id": "rec-1",
        "source_hash": source_hash,
        "provenance_file": "source/upload.txt",
        "fields": [
            {
                "field": "sample_size",
                "status": "not_reported",
                "value": "",
                "span": None,
                "estimation_note": "",
            }
        ],
        # An extraneous field this function never validates on its own --
        # exactly the shape the guard's own comment in server.py describes --
        # carrying a non-finite float that only `_object_hash`'s
        # `allow_nan=False` serialization ever touches.
        "engine_debug_note": float("nan"),
        # A well-formed-looking (but definitely wrong) sha256 hex digest, so
        # the only way this reaches a mismatch is through the hash
        # computation itself, not the artifact's own `_SHA256_HEX` shape
        # check.
        "record_hash": "0" * 64,
    }
    path = tmp_path / "extraction.json"
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")

    with pytest.raises(BridgeError, match="record_hash does not match its content"):
        _validate_extraction_artifact(
            path,
            source_bytes=source_bytes,
            record_id="rec-1",
            provenance_file="source/upload.txt",
            requested_fields=[{"field": "sample_size", "status": "not_reported"}],
        )


def test_validate_rob_artifact_reports_hash_mismatch_not_nan_crash(tmp_path):
    """The same guard as
    `test_validate_extraction_artifact_reports_hash_mismatch_not_nan_crash`
    above, for `_validate_rob_artifact`'s equivalent `rob_hash` check: a
    body containing a non-finite float anywhere must be reported as
    `BridgeError("risk-of-bias artifact rob_hash does not match its
    content")`, not let `json.dumps`'s `allow_nan=False` `TypeError`/
    `ValueError` escape unhandled. Reaching this guard likewise requires
    writing a malformed artifact file directly rather than going through
    `_install_stub_bridge`'s `rob_output_mutation` hook, which -- like
    `extract_output_mutation` -- computes `rob_hash` via the same
    `_object_hash` helper *before* the mutated body ever reaches this
    function, so a NaN/Infinity mutation would make the stub's own fixture
    construction raise first."""
    requested_spec = {
        "record_id": "rec-1",
        "tool": "RoB2",
        "overall_judgment": "low_risk",
        "domains": [
            {
                "domain": "Randomization process",
                "judgment": "low_risk",
                "rationale": "Central randomization described.",
                "source_links": [],
            }
        ],
    }
    body = {
        "version": "rob-assessment-v1",
        "record_id": "rec-1",
        "tool": "RoB2",
        "domains": [
            {
                "domain": "Randomization process",
                "judgment": "low_risk",
                "rationale": "Central randomization described.",
                "source_links": [],
            }
        ],
        "overall_judgment": "low_risk",
        # Same extraneous, unvalidated NaN-carrying field as the extraction
        # test above.
        "engine_debug_note": float("nan"),
        "rob_hash": "0" * 64,
    }
    path = tmp_path / "rob.json"
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")

    with pytest.raises(BridgeError, match="rob_hash does not match its content"):
        _validate_rob_artifact(
            path,
            requested_spec=requested_spec,
            extraction_record_id="rec-1",
        )
