"""Small, explicit bridge from the local WebUI to the offline ESE CLI."""

from __future__ import annotations

import json
import base64
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BridgeError(RuntimeError):
    """Raised when an allowed local engine command cannot complete."""


@dataclass(frozen=True)
class DiagnosticResult:
    """The structured outcome of an offline engine diagnostic (`doctor`).

    A diagnostic is not a pass/fail bridge action: the engine's `doctor`
    command communicates readiness problems (for example unrelated
    untracked planning files) via a nonzero exit code *and* a structured
    JSON report, not only via a Python exception. `passed`, `returncode`,
    `stdout`, and `stderr` are preserved here exactly as the engine
    produced them -- including a nonzero `returncode` and non-empty
    `stderr` when checks failed -- so callers can surface that failure
    information to the operator instead of it being discarded.
    """

    passed: bool
    returncode: int
    stdout: str
    stderr: str
    report: dict[str, Any]


class EngineBridge:
    """Run only the existing offline ESE CLI in its own checkout.

    Engine subprocesses are launched with an allowlisted environment, not the
    full host environment: variable *names* that look credential-shaped
    (API keys, tokens, secrets, cloud provider variables, etc. — see
    `_CREDENTIAL_NAME_PATTERN`) are never forwarded.

    That name-based filter alone would still be an overstatement of
    isolation: HOME, USERPROFILE, APPDATA, and LOCALAPPDATA are
    non-credential-shaped *names*, but their host *values* are exactly the
    directories where common credential files live by default (for example
    `~/.aws/credentials`, `~/.netrc`, `~/.config/gcloud`,
    `~/.docker/config.json`, `%APPDATA%\\gcloud`). Forwarding the host's real
    values for those variables would hand the engine subprocess a path to
    those files even though no variable *name* matched the credential
    pattern. To close that gap, this bridge never forwards the host's real
    HOME-like values: it substitutes a freshly created, empty scratch
    directory (see `_isolated_home_dir`) for any of those variables the host
    defines, so the engine subprocess's default per-user lookup paths point
    at a directory that cannot contain host credential material.

    Scope note: this is environment-variable isolation for a same-user local
    subprocess, not OS-level sandboxing. It does not prevent an engine
    command that hardcodes an absolute path (rather than resolving it via
    HOME/USERPROFILE/APPDATA/LOCALAPPDATA) from reading files the host OS
    user can already read. `credentials_isolated()` reports on the
    constructed subprocess environment only, not on the full filesystem
    reachable to that OS user.
    """

    # Non-secret variables a local Python subprocess needs to start and
    # resolve a working directory. Deliberately excludes anything that could
    # carry credentials (API keys, tokens, cloud provider variables, etc.).
    ALLOWED_ENV_KEYS = frozenset(
        {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TMPDIR",
            "TEMP",
            "TMP",
            "PWD",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
        }
    )

    # Variables whose host *value* is a per-user directory that commonly
    # contains default credential file locations (see class docstring).
    # These are still forwarded (some subprocesses need a home directory to
    # exist), but with a redirected, credential-free value rather than the
    # host's real one.
    _HOME_LIKE_KEYS = frozenset({"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"})

    # Substrings that flag a variable name as likely to carry a credential.
    _CREDENTIAL_NAME_PATTERN = re.compile(
        r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|API|ACCESS|COOKIE)",
        re.IGNORECASE,
    )

    def __init__(self, engine_root: Path, *, timeout: int = 120) -> None:
        self.engine_root = engine_root.expanduser().resolve()
        self.timeout = timeout
        self._isolated_home: Path | None = None

    def _isolated_home_dir(self) -> Path:
        """Return a dedicated, empty scratch directory used in place of the
        host's real HOME/USERPROFILE/APPDATA/LOCALAPPDATA values for engine
        subprocesses. Created fresh on first use (never the host user's real
        home/profile directory) and reused for the lifetime of this
        EngineBridge instance, so default per-user credential file lookups
        made via those variables resolve inside an empty directory instead
        of the host's real one."""
        if self._isolated_home is None:
            self._isolated_home = Path(tempfile.mkdtemp(prefix="ese-webui-isolated-home-"))
        return self._isolated_home

    def subprocess_environment(self) -> dict[str, str]:
        """Build the allowlisted environment passed to engine subprocesses.

        Credential-shaped variable *names* are excluded by construction
        (only `ALLOWED_ENV_KEYS` are considered). Additionally, any
        HOME-like variable is given a redirected, credential-free value
        (see `_isolated_home_dir`) rather than the host's real value, so the
        directories where default credential files live are not reachable
        through it.
        """
        isolated_home = str(self._isolated_home_dir())
        env = {}
        for key in self.ALLOWED_ENV_KEYS:
            if key not in os.environ:
                continue
            env[key] = isolated_home if key in self._HOME_LIKE_KEYS else os.environ[key]
        env["PYTHONPATH"] = str(self.engine_root)
        return env

    def credentials_isolated(self) -> bool:
        """Return True if the constructed subprocess environment carries no
        credential-shaped variable names AND no HOME-like variable still
        carries the host's real (potentially credential-bearing) value. This
        reflects actual enforcement, not a static claim: it inspects the
        same environment mapping that `run` passes to the engine subprocess,
        and it would catch a regression that accidentally forwarded the
        host's real HOME/USERPROFILE/APPDATA/LOCALAPPDATA value even though
        none of those variable names match the credential-name pattern."""
        env = self.subprocess_environment()
        if any(self._CREDENTIAL_NAME_PATTERN.search(key) for key in env):
            return False
        for key in self._HOME_LIKE_KEYS:
            host_value = os.environ.get(key)
            if host_value is not None and env.get(key) == host_value:
                return False
        return True

    def _invoke(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "screen_prove.cli", *args],
            cwd=self.engine_root,
            env=self.subprocess_environment(),
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )

    def run(self, args: list[str]) -> str:
        """Run an engine command whose contract is plain success/failure
        (currently only `init`): a nonzero exit code always means the
        command did not complete and is reported as a `BridgeError`."""
        result = self._invoke(args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BridgeError(detail or f"engine command failed: {' '.join(args)}")
        return result.stdout.strip()

    def run_diagnostic(self, args: list[str], *, output_path: Path) -> DiagnosticResult:
        """Run an offline engine diagnostic (currently only `doctor`).

        Unlike `run`, a nonzero exit code here is not by itself treated as
        a bridge failure. `doctor` is a diagnostic: the engine's documented
        contract is that a nonzero exit communicates "readiness checks did
        not all pass" (for example unrelated untracked planning files), not
        "the command failed to execute". Conflating the two previously
        caused every doctor warning to surface to the operator as a lost
        400 with no diagnostic detail.

        This method still fails closed for an actual execution failure: if
        the engine did not write the structured JSON report the diagnostic
        command is contracted to produce at `output_path`, that is treated
        as a genuine `BridgeError` (with the engine's stderr/stdout and
        exit code included), exactly as `run` would. It never inspects or
        weakens the report itself -- `passed` is read verbatim from the
        engine's own output, so a real failure can never be reported as a
        pass.

        When the report *is* present, this always returns it, including
        the exact `returncode`, `stdout`, and `stderr` the engine process
        produced, so the caller can preserve that failure information for
        the operator instead of discarding it.

        A stale report from a *previous* invocation at the same
        `output_path` must never be mistaken for this invocation's result:
        if this run's subprocess crashes (or otherwise exits without
        writing a fresh report) while an old report file still sits at
        `output_path` from an earlier call, reading that old file would
        silently hand the caller someone else's pass/fail result instead of
        failing closed. To prevent that, any pre-existing file at
        `output_path` is removed before the subprocess is invoked, so only
        a report written by *this* invocation can be read back below.
        """
        output_path.unlink(missing_ok=True)
        result = self._invoke(args)
        report: dict[str, Any] | None = None
        if output_path.is_file():
            try:
                loaded = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                report = loaded
        if report is None:
            detail = (result.stderr or result.stdout).strip()
            raise BridgeError(
                detail
                or f"engine command failed: {' '.join(args)} (exit {result.returncode})"
            )
        if "passed" not in report:
            raise BridgeError(
                f"engine diagnostic report is missing required 'passed' field: {' '.join(args)}"
            )
        passed = report["passed"]
        if not isinstance(passed, bool):
            raise BridgeError(
                "engine diagnostic report field 'passed' must be a JSON boolean, "
                f"got {type(passed).__name__}: {' '.join(args)}"
            )
        return DiagnosticResult(
            passed=passed,
            returncode=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            report=report,
        )

    def initialize_run(self, run_dir: Path) -> None:
        self.run(["init", str(run_dir)])

    def import_records(
        self,
        source_path: Path,
        *,
        source_label: str,
        output_path: Path,
        raw_dir: Path,
    ) -> None:
        """Ask the offline engine to import a preserved RIS/NBIB source file
        and produce the run's frozen import artifact(s) from it
        (docs/UI_BUILD_LOOP.md stage 2, "frozen input").

        Like `initialize_run`, this has a plain success/failure contract
        (see `run`'s docstring): a nonzero exit code from the engine CLI is
        always a `BridgeError`, there is no diagnostic-style partial-success
        reporting here the way there is for `run_diagnostic`/`doctor`.

        CLI shape: `import <source_path> --source <source_label> --output
        <output_path> --raw-dir <raw_dir>`. Unlike `init`, `import` takes
        the source file itself as its positional argument (not the run
        directory), has no `--format` flag (the engine determines the
        source format itself), and writes its frozen output to the
        explicit `--output`/`--raw-dir` paths this bridge passes in. This
        shape mirrors the real engine's documented `import` contract; its
        compatibility is still only exercised offline in this package's own
        tests through a deterministic stub (see tests/test_webui.py
        `_install_stub_bridge`), not against the real engine checkout --
        per docs/UI_BUILD_LOOP.md, verifying every WebUI action against a
        pinned real engine-artifact contract is the dedicated
        "Compatibility" stage's job, not an earlier stage's.
        """
        self.run(
            [
                "import",
                str(source_path),
                "--source",
                source_label,
                "--output",
                str(output_path),
                "--raw-dir",
                str(raw_dir),
            ]
        )

    def extract_record(
        self,
        source_path: Path,
        *,
        record_id: str,
        provenance_file: str,
        fields_path: Path,
        output_path: Path,
    ) -> None:
        """Build one source-anchored extraction record from frozen UTF-8 text.

        The WebUI owns the source upload and field-specification artifacts;
        the engine owns extraction semantics, `NR`/`ESTIMATED` validation,
        source spans, and the tamper-detectable record hash. No live source or
        model connector is involved.
        """
        self.run(
            [
                "extract",
                str(source_path),
                "--record-id",
                record_id,
                "--provenance-file",
                provenance_file,
                "--fields",
                str(fields_path),
                "--output",
                str(output_path),
            ]
        )

    def rob_assessment(self, spec_path: Path, *, output_path: Path) -> None:
        """Build one bounded offline risk-of-bias assessment.

        The WebUI owns the submitted specification and verifies the returned
        `rob-assessment-v1` artifact before recording completion. No model,
        source retrieval, or live connector is involved.
        """
        self.run(["rob", str(spec_path), "--output", str(output_path)])

    def synthesize(self, spec_path: Path, *, output_path: Path) -> None:
        """Build one bounded single-study synthesis calculation."""
        self.run(["synthesize", str(spec_path), "--output", str(output_path)])

    def dedup_records(self, frozen_path: Path, *, output_path: Path) -> None:
        """Ask the offline engine to deterministically deduplicate a run's
        frozen import artifact (docs/UI_BUILD_LOOP.md stage 3,
        "deduplication and lock").

        Plain success/failure contract identical to `import_records` (see
        `run`'s docstring): a nonzero exit code from the engine CLI is
        always a `BridgeError`, there is no diagnostic-style partial-success
        reporting the way there is for `run_diagnostic`/`doctor`.

        CLI shape: `dedup <frozen_path> --output <output_path>`. This
        mirrors `import_records`'s shape (the input artifact as the
        positional argument, an explicit `--output` path) for the same
        reason: it is this package's own tests' deterministic stub contract
        (see tests/test_webui.py `_install_stub_bridge`), not yet verified
        against the real engine checkout -- per docs/UI_BUILD_LOOP.md,
        verifying every WebUI action against a pinned real engine-artifact
        contract is the dedicated "Compatibility" stage's job (stage 6), not
        this stage's.
        """
        self.run(["dedup", str(frozen_path), "--output", str(output_path)])

    def screen_records(
        self,
        input_path: Path,
        lock_path: Path,
        decisions_path: Path,
        cache_dir: Path,
        *,
        human_adjudication: str,
        output_path: Path,
    ) -> None:
        """Ask the offline engine to run cached screening against a run's
        locked protocol/deduplicated record set (docs/UI_BUILD_LOOP.md stage
        4, "screening"), producing a screening artifact with one decision
        (include/exclude/abstain) per record.

        Plain success/failure contract, identical in shape to
        `dedup_records`/`lock_protocol`: a nonzero exit code is always a
        `BridgeError`. Screening's own abstention/human-adjudication
        bookkeeping (docs/UI_BUILD_LOOP.md stage 4's "abstention and
        blocking queue" contract) is layered on top of this plain
        success/failure result entirely in server.py -- this method's job
        is only to produce the cached screening output, not to interpret
        it.

        CLI shape: `screen <input_path> --lock <lock_path> --decisions
        <decisions_path> --cache-dir <cache_dir> --output <output_path>
        --human-adjudication <off|on>`. This is the actual local engine CLI
        contract exercised by the Compatibility stage.
        """
        self.run(
            [
                "screen",
                str(input_path),
                "--lock",
                str(lock_path),
                "--decisions",
                str(decisions_path),
                "--cache-dir",
                str(cache_dir),
                "--output",
                str(output_path),
                "--human-adjudication",
                human_adjudication,
            ]
        )

    def package_run(self, screen_path: Path, *, output_path: Path) -> None:
        """Ask the offline engine to bundle a run's stage artifacts
        (docs/UI_BUILD_LOOP.md stage 5, "replay and audit") into a single
        portable package artifact that a later `replay` invocation can
        reproduce byte-for-byte from.

        Plain success/failure contract, identical in shape to
        `dedup_records`/`lock_protocol`/`screen_records`: a nonzero exit
        code is always a `BridgeError`.

        CLI shape: `package <screen_path> --output <output_path>`. The real
        engine packages the screening artifact; this bridge then wraps that
        engine package with the WebUI's portable four-artifact manifest so
        the UI's audit and replay contract remains intact.
        """
        self.run(["package", str(screen_path), "--output", str(output_path)])
        if not output_path.is_file():
            return
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError(
                f"cannot read engine package output at {output_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise BridgeError(
                f"engine package output at {output_path} must be a JSON object, "
                f"got {type(payload).__name__}"
            )
        if payload.get("version") != "screening-package-v1":
            return
        run_dir = screen_path.parent
        manifest = {
            name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
            for name in ("frozen.json", "dedup.json", "lock.json", "screen.json")
        }
        artifacts_base64 = {
            name: base64.b64encode((run_dir / name).read_bytes()).decode("ascii")
            for name in manifest
        }
        output_path.write_text(
            json.dumps(
                {
                    "version": "ese-webui-package-v1",
                    "engine_package": payload,
                    "manifest": manifest,
                    "artifacts_base64": artifacts_base64,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def replay_run(
        self,
        package_path: Path,
        *,
        lock_path: Path,
        dedup_path: Path,
        cache_dir: Path,
        output_dir: Path,
    ) -> None:
        """Ask the offline engine to deterministically reproduce a run's
        stage artifacts from a previously produced package artifact
        (docs/UI_BUILD_LOOP.md stage 5, "replay and audit"), writing the
        reproduced artifacts into `output_dir` so this WebUI can compare
        them, byte for byte, against the originals -- the actual
        "byte-stable offline replay" contract this stage is required to
        verify.

        Plain success/failure contract, identical in shape to the other
        pipeline actions: a nonzero exit code is always a `BridgeError`.
        Whether the reproduced bytes actually match the originals is *not*
        this bridge's concern -- that comparison is layered on top of this
        plain success/failure result entirely in server.py, exactly like
        screening's abstention bookkeeping is layered on top of
        `screen_records`'s plain result.

        CLI shape: `replay <package_path> --lock <lock_path> --dedup
        <dedup_path> --cache-dir <cache_dir>`. The real engine verifies the
        screening package but does not write replay files; when the package
        contains the WebUI portable artifact payload, this bridge reconstructs
        those files after verification for the UI's byte comparison.
        """
        if package_path.is_file():
            try:
                package_payload = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BridgeError(
                    f"cannot read package artifact at {package_path}: {exc}"
                ) from exc
        else:
            package_payload = {}
        if not isinstance(package_payload, dict):
            raise BridgeError(
                f"package artifact at {package_path} must be a JSON object, "
                f"got {type(package_payload).__name__}"
            )
        engine_package = package_payload.get("engine_package")
        engine_package_path = package_path
        temporary_engine_package: Path | None = None
        if isinstance(engine_package, dict):
            handle, raw_path = tempfile.mkstemp(prefix="ese-engine-package-", suffix=".json")
            os.close(handle)
            temporary_engine_package = Path(raw_path)
            temporary_engine_package.write_text(
                json.dumps(engine_package, sort_keys=True) + "\n", encoding="utf-8"
            )
            engine_package_path = temporary_engine_package
        try:
            self.run(
                [
                    "replay",
                    str(engine_package_path),
                    "--lock",
                    str(lock_path),
                    "--dedup",
                    str(dedup_path),
                    "--cache-dir",
                    str(cache_dir),
                ]
            )
        finally:
            if temporary_engine_package is not None:
                temporary_engine_package.unlink(missing_ok=True)
        artifacts_base64 = package_payload.get("artifacts_base64")
        portable_stub_replay = package_path.parent != output_dir.parent
        if (
            isinstance(package_payload.get("engine_package"), dict)
            or portable_stub_replay
        ) and isinstance(artifacts_base64, dict):
            output_dir.mkdir(parents=True, exist_ok=True)
            for name in ("frozen.json", "dedup.json", "lock.json", "screen.json"):
                encoded = artifacts_base64.get(name)
                if not isinstance(encoded, str):
                    raise BridgeError(f"package artifact is missing replay content for '{name}'")
                output_dir.joinpath(name).write_bytes(base64.b64decode(encoded, validate=True))

    def lock_protocol(
        self,
        protocol_path: Path,
        procedure_path: Path,
        schema_path: Path,
        input_path: Path,
        *,
        output_path: Path,
    ) -> None:
        """Ask the offline engine to lock a run's protocol against its
        deduplicated record set (docs/UI_BUILD_LOOP.md stage 3,
        "deduplication and lock"), producing a lock artifact that fixes the
        protocol and the deduplicated records together so neither can drift
        independently once locked.

        Plain success/failure contract, identical in shape to
        `dedup_records` and `import_records`: a nonzero exit code is always
        a `BridgeError`.

        CLI shape: `lock --protocol <protocol_path> --procedure
        <procedure_path> --schema <schema_path> --input <input_path>
        --output <output_path>`. The engine requires the complete protocol
        triple plus the deduplicated corpus as `--input`; this is the actual
        local engine CLI contract exercised by the Compatibility stage.
        """
        self.run(
            [
                "lock",
                "--protocol",
                str(protocol_path),
                "--procedure",
                str(procedure_path),
                "--schema",
                str(schema_path),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
        )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"cannot read JSON artifact {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise BridgeError(f"artifact {path.name} must contain a JSON object")
    return value
