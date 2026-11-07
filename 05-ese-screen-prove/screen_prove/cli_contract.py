"""CLI command-shape contract for the run-level extraction, risk-of-bias, and
synthesis CLI surfaces: stage 10 of the work-package queue in
`docs/AUTONOMOUS_BUILD_LOOP.md`.

`screen_prove.cli` already exposes dedicated `extract`, `rob`, and
`synthesize` subcommands, but the documented engine/WebUI artifact contract
in `screen_prove.contract` previously left their `cli_command` fields blank
(as if no dedicated subcommand existed yet), and no offline check exercised
those three subcommands' actual command-line surface end to end -- every
existing fixture (`screen_prove.contract.build_compatibility_fixture`,
`screen_prove.qualification.check_extraction_replay`/
`check_risk_of_bias_replay`/`check_synthesis_replay`) called
`extraction.build_extraction_record`, `risk_of_bias.build_rob_assessment`,
and `synthesis.calculate_effect_measure` directly, never through
`screen_prove.cli.main`'s argv parsing, JSON-argument-file handling, and
process exit code.

This module closes that gap without adding any new engine calculation
behavior: `CLI_COMMAND_CONTRACTS` documents each subcommand's exact
positional/flag argv shape and the `verified_by` function a caller
re-verifies its on-disk JSON output with (the same artifact-only verifier
`screen_prove.contract.ARTIFACT_CONTRACTS` names for that artifact);
`build_cli_command_fixtures` drives the real `extract`, `rob`, and
`synthesize` subcommands through `screen_prove.cli.main` against a fixed,
scratch temporary directory, reads each command's on-disk JSON output, and
re-verifies it; `verify_cli_command_fixtures` re-verifies an "at rest"
bundle from nothing but its own content, the same self-hash pattern
`screen_prove.contract.verify_compatibility_fixture` uses (see that
module's docstring for the boundary a self-hash check can and cannot
prove: it detects corruption/single-artifact tampering, not authenticity
against a caller able to rewrite the whole bundle); `build_cli_contract_receipt`
aggregates the command-shape description and one concrete fixture bundle
into a byte-stable receipt.

No network access, no live credentials, and no new engine calculation
behavior is added here -- every command is exercised through the existing
`screen_prove.cli.main` entry point, and every artifact is verified through
the existing `verify_*` functions of the already-completed extraction,
risk-of-bias, and synthesis stages.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping

from .cli import main as cli_main
from .contracts import IntegrityError, object_hash, read_json, write_json
from .extraction import verify_extraction_record
from .risk_of_bias import verify_rob_assessment
from .synthesis import verify_calculation

CLI_CONTRACT_VERSION = "cli-contract-v1"
CLI_CONTRACT_RECEIPT_VERSION = "cli-contract-receipt-v1"

# Every run-level extraction/risk-of-bias/synthesis CLI surface this offline
# engine exposes, keyed by the same artifact name
# `screen_prove.contract.ARTIFACT_CONTRACTS` uses. `command` is the exact
# `screen-prove` subcommand name; `argv_shape` is the ordered, documented
# `screen_prove.cli.main` argv this module drives in
# `build_cli_command_fixtures`, with `<...>` placeholders standing in for
# the fixture's own scratch file paths and identifiers (substituted at
# build time -- this dict never embeds a live value or path); `verified_by`
# is the module-qualified artifact-only verifier a caller re-checks the
# command's on-disk JSON output with (a single argument, no file I/O, no
# additional context) -- the same function
# `screen_prove.contract.ARTIFACT_CONTRACTS` documents for that artifact.
CLI_COMMAND_CONTRACTS: dict[str, dict[str, Any]] = {
    "extraction_record": {
        "command": "extract",
        "argv_shape": [
            "extract",
            "<source>",
            "--record-id",
            "<record-id>",
            "--provenance-file",
            "<provenance-file>",
            "--fields",
            "<fields.json>",
            "--output",
            "<output.json>",
        ],
        "verified_by": "screen_prove.extraction.verify_extraction_record",
    },
    "risk_of_bias_assessment": {
        "command": "rob",
        "argv_shape": ["rob", "<spec.json>", "--output", "<output.json>"],
        "verified_by": "screen_prove.risk_of_bias.verify_rob_assessment",
    },
    "synthesis_calculation": {
        "command": "synthesize",
        "argv_shape": ["synthesize", "<spec.json>", "--output", "<output.json>"],
        "verified_by": "screen_prove.synthesis.verify_calculation",
    },
}


def describe_cli_contract() -> dict[str, Any]:
    """Return the byte-stable, documented CLI command-shape contract for the
    run-level extraction, risk-of-bias, and synthesis subcommands."""
    body = {"version": CLI_CONTRACT_VERSION, "commands": CLI_COMMAND_CONTRACTS}
    return {**body, "contract_hash": object_hash(body)}


_FIXTURE_FULL_TEXT = "Sample size was 240 participants."


def build_cli_command_fixtures() -> dict[str, Any]:
    """Drive the real `extract`, `rob`, and `synthesize` subcommands through
    `screen_prove.cli.main` against a fixed, scratch temporary directory,
    and return a bundle of their verified on-disk JSON outputs.

    Every command is invoked exactly as a real caller would invoke it from a
    shell -- a plain argv list, JSON argument files written to disk, and a
    JSON output file read back afterward -- not by calling the underlying
    engine function directly. Raises `IntegrityError` if any subcommand
    exits non-zero or if its output artifact fails its own `verified_by`
    re-verification. No I/O outlives this call except a scratch temporary
    directory that is discarded before it returns.
    """
    with tempfile.TemporaryDirectory(prefix="screen-prove-cli-contract-") as tmp:
        tmp_path = Path(tmp)

        source_path = tmp_path / "fulltext.txt"
        source_path.write_text(_FIXTURE_FULL_TEXT, encoding="utf-8")
        fields_path = tmp_path / "fields.json"
        write_json(fields_path, [{"field": "sample_size", "start": 16, "end": 19}])
        extraction_output = tmp_path / "extraction.json"
        extract_exit = cli_main(
            [
                "extract",
                str(source_path),
                "--record-id",
                "cli-contract:extraction:1",
                "--provenance-file",
                "cli-contract-fulltext.txt",
                "--fields",
                str(fields_path),
                "--output",
                str(extraction_output),
            ]
        )
        if extract_exit != 0:
            raise IntegrityError(f"'screen-prove extract' exited {extract_exit}")
        extraction_record = read_json(extraction_output)
        verify_extraction_record(extraction_record)

        rob_spec_path = tmp_path / "rob.json"
        write_json(
            rob_spec_path,
            {
                "record_id": "cli-contract:rob:1",
                "tool": "protocol-rob-v1",
                "domains": [
                    {
                        "domain": "randomization_process",
                        "judgment": "low_risk",
                        "rationale": "computer-generated sequence reported",
                        "source_links": ["Methods p.2"],
                    }
                ],
                "overall_judgment": "low_risk",
            },
        )
        rob_output = tmp_path / "rob-output.json"
        rob_exit = cli_main(["rob", str(rob_spec_path), "--output", str(rob_output)])
        if rob_exit != 0:
            raise IntegrityError(f"'screen-prove rob' exited {rob_exit}")
        rob_assessment = read_json(rob_output)
        verify_rob_assessment(rob_assessment)

        synthesis_spec_path = tmp_path / "synthesis.json"
        write_json(
            synthesis_spec_path,
            {
                "record_id": "cli-contract:synthesis:1",
                "measure": "risk_difference",
                "counts": {
                    "treated_events": 30,
                    "treated_total": 150,
                    "control_events": 15,
                    "control_total": 150,
                },
            },
        )
        synthesis_output = tmp_path / "synthesis-output.json"
        synthesis_exit = cli_main(
            ["synthesize", str(synthesis_spec_path), "--output", str(synthesis_output)]
        )
        if synthesis_exit != 0:
            raise IntegrityError(f"'screen-prove synthesize' exited {synthesis_exit}")
        synthesis_calculation = read_json(synthesis_output)
        verify_calculation(synthesis_calculation)

    artifacts = {
        "extraction_record": extraction_record,
        "risk_of_bias_assessment": rob_assessment,
        "synthesis_calculation": synthesis_calculation,
    }
    body = {"version": CLI_CONTRACT_VERSION, "artifacts": artifacts}
    return {**body, "bundle_hash": object_hash(body)}


def verify_cli_command_fixtures(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Verify an "at rest" CLI command fixture bundle from its own content.

    Raises `IntegrityError` if `bundle_hash` does not match the bundle's
    content (corruption detection), if the bundle's `version` is
    unsupported, if `artifacts` does not carry exactly the keys
    `CLI_COMMAND_CONTRACTS` documents, or if any individual artifact fails
    its own `verified_by` re-verification. Like every other
    `_hash`/`bundle_hash` check in this codebase, this is a self-hash
    comparison: it proves the checked content is internally consistent with
    its own recorded hash, not that it was produced by a trusted party (see
    `screen_prove.contract`'s module docstring for the same caveat on its
    `verify_compatibility_fixture`).
    """
    body = {key: value for key, value in bundle.items() if key != "bundle_hash"}
    if bundle.get("bundle_hash") != object_hash(body):
        raise IntegrityError("bundle_hash does not match CLI command fixture content")
    if body.get("version") != CLI_CONTRACT_VERSION:
        raise IntegrityError(f"unsupported CLI contract version: {body.get('version')}")
    artifacts = body.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(CLI_COMMAND_CONTRACTS):
        raise IntegrityError("CLI command fixture does not cover every contracted CLI surface")

    verify_extraction_record(artifacts["extraction_record"])
    verify_rob_assessment(artifacts["risk_of_bias_assessment"])
    verify_calculation(artifacts["synthesis_calculation"])
    return body


def build_cli_contract_receipt() -> dict[str, Any]:
    """Build a byte-stable receipt documenting the CLI command-shape contract
    and proving, via one concrete CLI-driven fixture bundle, that the
    `extract`, `rob`, and `synthesize` subcommands still build and verify
    real artifacts through `screen_prove.cli.main` itself.
    """
    contract = describe_cli_contract()
    try:
        fixture = build_cli_command_fixtures()
        verify_cli_command_fixtures(fixture)
    except IntegrityError as exc:
        body = {
            "version": CLI_CONTRACT_RECEIPT_VERSION,
            "contract": contract,
            "fixture": None,
            "detail": f"CLI command fixture failed: {exc}",
            "passed": False,
        }
        return {**body, "receipt_hash": object_hash(body)}
    body = {
        "version": CLI_CONTRACT_RECEIPT_VERSION,
        "contract": contract,
        "fixture": fixture,
        "detail": "CLI command fixtures built and verified for every contracted CLI surface",
        "passed": True,
    }
    return {**body, "receipt_hash": object_hash(body)}
