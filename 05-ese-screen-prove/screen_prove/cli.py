"""Small command-line surface for the offline Screen-and-Prove workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contract import build_contract_receipt
from .contracts import ProtocolLock, read_json, sha256_bytes, sha256_file, write_json
from .dedup import deduplicate
from .doctor import run_doctor
from .engine_offline import (
    ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH,
    build_engine_offline_receipt,
    write_engine_offline_evidence,
)
from .evaluation import compare_package, seal_labels, verify_sealed_labels
from .imports import freeze_import
from .extraction import (
    build_extraction_record,
    estimated_field,
    extract_field,
    not_reported_field,
)
from .qualification import build_qualification_receipt
from .risk_of_bias import build_rob_assessment
from .replay import _verified_package, verify_replay, write_package
from .screening import StaticDecisionAdapter, build_package, screen_records
from .synthesis import calculate_effect_measure, build_analysis_recipe
from .validation import build_validation_receipt


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _extraction_fields(full_text: str, source_hash: str, provenance_file: str, raw_fields: object) -> list[dict]:
    if not isinstance(raw_fields, list):
        raise ValueError("extraction fields must be a JSON array")
    fields: list[dict] = []
    for entry in raw_fields:
        if not isinstance(entry, dict):
            raise ValueError("each extraction field must be a JSON object")
        status = entry.get("status", "reported")
        field = entry.get("field", "")
        allowed = {
            "reported": {"field", "status", "start", "end"},
            "not_reported": {"field", "status"},
            "estimated": {"field", "status", "value", "estimation_note"},
        }.get(status)
        if allowed is None:
            raise ValueError(f"unknown extraction status: {status!r}")
        forbidden = sorted(set(entry) - allowed)
        if forbidden:
            raise ValueError(
                f"fields {forbidden!r} are not allowed for extraction status {status!r}"
            )
        if status == "reported":
            fields.append(
                extract_field(
                    full_text,
                    source_hash,
                    provenance_file,
                    field,
                    entry.get("start"),
                    entry.get("end"),
                )
            )
        elif status == "not_reported":
            fields.append(not_reported_field(field))
        elif status == "estimated":
            fields.append(
                estimated_field(field, entry.get("value", ""), entry.get("estimation_note", ""))
            )
    return fields


def _rob_assessment_spec(raw_spec: object) -> dict:
    if not isinstance(raw_spec, dict):
        raise ValueError("risk-of-bias specification must be a JSON object")
    required = {"record_id", "tool", "domains", "overall_judgment"}
    missing = sorted(required - set(raw_spec))
    extra = sorted(set(raw_spec) - required)
    if missing:
        raise ValueError(f"risk-of-bias specification is missing fields: {missing!r}")
    if extra:
        raise ValueError(f"risk-of-bias specification has unsupported fields: {extra!r}")
    domains = raw_spec["domains"]
    if not isinstance(domains, list):
        raise ValueError("risk-of-bias domains must be a JSON array")
    normalized_domains: list[dict] = []
    allowed_domain = {"domain", "judgment", "rationale", "source_links"}
    for entry in domains:
        if not isinstance(entry, dict):
            raise ValueError("each risk-of-bias domain must be a JSON object")
        extra_domain = sorted(set(entry) - allowed_domain)
        if extra_domain:
            raise ValueError(f"risk-of-bias domain has unsupported fields: {extra_domain!r}")
        normalized_domains.append(
            {
                "domain": entry.get("domain"),
                "judgment": entry.get("judgment"),
                "rationale": entry.get("rationale"),
                "source_links": entry.get("source_links", []),
            }
        )
    return build_rob_assessment(
        raw_spec["record_id"],
        raw_spec["tool"],
        normalized_domains,
        raw_spec["overall_judgment"],
    )


def _synthesis_spec(raw_spec: object) -> dict:
    if not isinstance(raw_spec, dict):
        raise ValueError("synthesis specification must be a JSON object")
    required = {"record_id", "measure", "counts"}
    missing = sorted(required - set(raw_spec))
    extra = sorted(set(raw_spec) - required)
    if missing:
        raise ValueError(f"synthesis specification is missing fields: {missing!r}")
    if extra:
        raise ValueError(f"synthesis specification has unsupported fields: {extra!r}")
    counts = raw_spec["counts"]
    if not isinstance(counts, dict):
        raise ValueError("synthesis counts must be a JSON object")
    required_counts = {"treated_events", "treated_total", "control_events", "control_total"}
    missing_counts = sorted(required_counts - set(counts))
    extra_counts = sorted(set(counts) - required_counts)
    if missing_counts:
        raise ValueError(f"synthesis counts are missing fields: {missing_counts!r}")
    if extra_counts:
        raise ValueError(f"synthesis counts have unsupported fields: {extra_counts!r}")
    recipe = build_analysis_recipe(raw_spec["record_id"], raw_spec["measure"])
    return calculate_effect_measure(recipe, counts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="screen-prove")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("directory", nargs="?", default=".")

    lock = sub.add_parser("lock")
    for name in ("protocol", "procedure", "schema", "input", "output"):
        lock.add_argument(f"--{name}", required=True)

    imp = sub.add_parser("import")
    imp.add_argument("source")
    imp.add_argument("--source", dest="source_name", required=True)
    imp.add_argument("--output", required=True)
    imp.add_argument("--raw-dir", required=True)

    extract = sub.add_parser("extract")
    extract.add_argument("source")
    extract.add_argument("--record-id", required=True)
    extract.add_argument("--provenance-file", required=True)
    extract.add_argument("--fields", required=True)
    extract.add_argument("--output", required=True)

    rob = sub.add_parser("rob")
    rob.add_argument("spec")
    rob.add_argument("--output", required=True)

    synthesize = sub.add_parser("synthesize")
    synthesize.add_argument("spec")
    synthesize.add_argument("--output", required=True)

    dedup = sub.add_parser("dedup")
    dedup.add_argument("input")
    dedup.add_argument("--output", required=True)

    screen = sub.add_parser("screen")
    screen.add_argument("input")
    screen.add_argument("--lock", required=True)
    screen.add_argument("--decisions", required=True)
    screen.add_argument("--cache-dir", required=True)
    screen.add_argument("--output", required=True)
    screen.add_argument("--human-adjudication", choices=("off", "on"), default="off")

    package = sub.add_parser("package")
    package.add_argument("input")
    package.add_argument("--output", required=True)

    compare = sub.add_parser("compare")
    compare.add_argument("package")
    compare.add_argument("--labels", required=True)
    compare.add_argument("--output", required=True)

    seal = sub.add_parser("seal")
    seal.add_argument("input")
    seal.add_argument("--output", required=True)

    replay = sub.add_parser("replay")
    replay.add_argument("package")
    replay.add_argument("--lock", required=True)
    replay.add_argument("--dedup", required=True)
    replay.add_argument("--cache-dir", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo-root", default=".")
    doctor.add_argument("--skip-tests", action="store_true")
    doctor.add_argument("--output")

    qualify = sub.add_parser("qualify")
    qualify.add_argument("--repo-root", default=".")
    qualify.add_argument("--skip-tests", action="store_true")
    qualify.add_argument("--output")

    validate = sub.add_parser("validate")
    validate.add_argument("--output")

    contract = sub.add_parser("contract")
    contract.add_argument("--output")

    cli_contract = sub.add_parser("cli-contract")
    cli_contract.add_argument("--output")

    offline_complete = sub.add_parser("offline-complete")
    offline_complete.add_argument("--repo-root", default=".")
    offline_complete.add_argument("--evidence")
    offline_complete.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        directory = _path(args.directory)
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "protocol.json", {
            "version": "protocol-v1",
            "role": "biomedical research assistant, not final scientific authority",
            "decision_vocabulary": ["include", "exclude", "abstain"],
        })
        write_json(directory / "procedure.json", {
            "version": "procedure-v1",
            "steps": ["freeze_import", "deduplicate", "screen", "package", "compare", "replay"],
        })
        write_json(directory / "schema.json", {
            "version": "schema-v1",
            "required_reference_fields": ["record_id", "title"],
            "required_screening_fields": ["record_id", "decision", "reason", "envelope"],
        })
        return 0
    if args.command == "lock":
        input_path = _path(args.input)
        input_json = read_json(input_path)
        input_hash = input_json.get("corpus_hash") if isinstance(input_json, dict) else None
        lock = ProtocolLock(read_json(_path(args.protocol)), read_json(_path(args.procedure)), read_json(_path(args.schema)), input_hash or sha256_file(input_path))
        write_json(_path(args.output), lock.to_dict())
        return 0
    if args.command == "import":
        freeze_import(_path(args.source), _path(args.output), _path(args.raw_dir), source_name=args.source_name)
        return 0
    if args.command == "extract":
        source_path = _path(args.source)
        source_bytes = source_path.read_bytes()
        full_text = source_bytes.decode("utf-8")
        source_hash = sha256_bytes(source_bytes)
        fields = _extraction_fields(
            full_text,
            source_hash,
            args.provenance_file,
            read_json(_path(args.fields)),
        )
        write_json(
            _path(args.output),
            build_extraction_record(args.record_id, source_hash, args.provenance_file, fields),
        )
        return 0
    if args.command == "rob":
        write_json(_path(args.output), _rob_assessment_spec(read_json(_path(args.spec))))
        return 0
    if args.command == "synthesize":
        write_json(_path(args.output), _synthesis_spec(read_json(_path(args.spec))))
        return 0
    if args.command == "dedup":
        write_json(_path(args.output), deduplicate(read_json(_path(args.input))))
        return 0
    if args.command == "screen":
        lock = ProtocolLock.from_dict(read_json(_path(args.lock)))
        decisions = read_json(_path(args.decisions))
        screening = screen_records(read_json(_path(args.input)), lock, StaticDecisionAdapter(decisions), _path(args.cache_dir), human_adjudication=args.human_adjudication)
        write_json(_path(args.output), screening)
        return 0
    if args.command == "package":
        write_package(_path(args.output), build_package(read_json(_path(args.input))))
        return 0
    if args.command == "seal":
        write_json(_path(args.output), seal_labels(read_json(_path(args.input))))
        return 0
    if args.command == "compare":
        sealed = verify_sealed_labels(_path(args.labels))
        report = compare_package(_verified_package(_path(args.package)), sealed)
        write_json(_path(args.output), report)
        return 0
    if args.command == "replay":
        verify_replay(_path(args.package), ProtocolLock.from_dict(read_json(_path(args.lock))), read_json(_path(args.dedup)), _path(args.cache_dir))
        return 0
    if args.command == "doctor":
        report = run_doctor(_path(args.repo_root), skip_tests=args.skip_tests)
        payload = report.to_dict()
        if args.output:
            write_json(_path(args.output), payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.passed else 1
    if args.command == "qualify":
        receipt = build_qualification_receipt(_path(args.repo_root), skip_tests=args.skip_tests)
        if args.output:
            write_json(_path(args.output), receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["passed"] else 1
    if args.command == "validate":
        receipt = build_validation_receipt()
        if args.output:
            write_json(_path(args.output), receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["passed"] else 1
    if args.command == "contract":
        receipt = build_contract_receipt()
        if args.output:
            write_json(_path(args.output), receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["passed"] else 1
    if args.command == "cli-contract":
        # Imported lazily to avoid a circular import: `cli_contract` drives
        # this module's own `main` entry point to exercise the CLI surface
        # end to end (see `screen_prove.cli_contract`'s module docstring).
        from .cli_contract import build_cli_contract_receipt

        receipt = build_cli_contract_receipt()
        if args.output:
            write_json(_path(args.output), receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["passed"] else 1
    if args.command == "offline-complete":
        repo_root = _path(args.repo_root)
        receipt = build_engine_offline_receipt(repo_root, skip_tests=False)
        if args.output:
            write_json(_path(args.output), receipt)
        if receipt["passed"]:
            evidence_path = _path(args.evidence) if args.evidence else repo_root / ENGINE_OFFLINE_RECEIPT_RELATIVE_PATH
            write_engine_offline_evidence(evidence_path, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["passed"] else 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
