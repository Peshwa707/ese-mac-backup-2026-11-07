#!/usr/bin/env python3
"""Validate and hash-seal Claude's blinded scientific outputs before unblinding."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_claude_secondary_review as runner  # noqa: E402


DEFAULT_BLIND_ROOT = runner.DEFAULT_BLIND_ROOT
MODULES = runner.MODULES
RUNNER_PATH = Path(runner.__file__).resolve()
API_SCHEMA_NOTE_FRAGMENT = (
    "exact counts, order, and maxima remain enforced by the local validator"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return bytes_sha256(encoded)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def claim_ids_for(blind_root: Path, module_code: str) -> list[str]:
    return [
        str(row["claim_id"])
        for row in runner.claims_for(blind_root, module_code)
    ]


def all_expected_claim_ids(blind_root: Path) -> list[str]:
    claims = [
        claim_id
        for module_code in [*MODULES.values(), "G"]
        for claim_id in claim_ids_for(blind_root, module_code)
    ]
    if len(claims) != len(set(claims)):
        raise ValueError("claim ledger contains duplicate claim ownership")
    return claims


def normalize_transport(value: object) -> str:
    transport = str(value)
    if transport == "anthropic_api":
        return "api"
    if transport in {"cli", "api"}:
        return transport
    raise ValueError(f"unknown Claude transport {transport!r}")


def find_provider_log(directory: Path, stage: str) -> Path:
    candidates = [
        directory / f"claude_cli_{stage}.json",
        directory / f"claude_api_{stage}.json",
    ]
    present = [path for path in candidates if path.is_file()]
    if len(present) != 1:
        raise ValueError(
            f"{directory}: expected exactly one final cli/api provider log for "
            f"{stage}, found {present}"
        )
    return present[0]


def require_nonnegative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a numeric value")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label}: expected a finite nonnegative value")
    return number


def require_positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label}: expected a positive integer")
    return value


def require_logged_file(
    provenance: dict[str, object],
    key: str,
    *,
    allowed_root: Path,
    label: str,
    expected: Path | None = None,
) -> Path:
    value = provenance.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}: final log does not bind {key}")
    logged = Path(value)
    if not logged.is_absolute():
        raise ValueError(f"{label}: {key} is not an absolute path")
    resolved = logged.resolve()
    root = allowed_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label}: {key} escapes {root}")
    if expected is not None and resolved != expected.resolve():
        raise ValueError(
            f"{label}: {key} is {resolved}, expected {expected.resolve()}"
        )
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def verify_logged_hash(
    provenance: dict[str, object],
    key: str,
    path: Path,
    *,
    label: str,
) -> str:
    recorded = provenance.get(key)
    actual = sha256(path)
    if not isinstance(recorded, str) or recorded != actual:
        raise ValueError(
            f"{label}: {key} does not match {path}; "
            f"recorded={recorded!r}, actual={actual}"
        )
    return actual


def validate_api_accounting(
    data: dict[str, object],
    provenance: dict[str, object],
    *,
    label: str,
) -> dict[str, object]:
    calculated = data.get("calculated_cost_usd")
    preflight = data.get("budget_preflight")
    configured_budget = provenance.get("max_budget_usd")

    if calculated is None and preflight is None:
        if configured_budget is not None:
            raise ValueError(
                f"{label}: legacy API log has a budget value without "
                "budget_preflight evidence"
            )
        return {
            "status": "legacy_api_budget_and_cost_unverified",
            "configured_budget_usd": None,
            "budget_enforcement": None,
            "provider_reported_cost_usd": None,
            "calculated_cost_usd": None,
            "effective_cost_usd": None,
            "cost_source": None,
            "limitation": (
                "This API call predates deterministic budget preflight and "
                "calculated-cost logging. It must not be counted as $0 or be "
                "described as having a verified $5 cap."
            ),
        }

    if calculated is None or not isinstance(preflight, dict):
        raise ValueError(
            f"{label}: API accounting is partial; calculated_cost_usd and "
            "budget_preflight must appear together"
        )
    calculated_cost = require_nonnegative_number(
        calculated, f"{label} calculated_cost_usd"
    )
    budget = require_nonnegative_number(
        preflight.get("budget_usd"), f"{label} budget_preflight.budget_usd"
    )
    provenance_budget = require_nonnegative_number(
        configured_budget, f"{label} execution_provenance.max_budget_usd"
    )
    if not math.isclose(budget, provenance_budget, abs_tol=1e-9):
        raise ValueError(f"{label}: API budget preflight and provenance disagree")

    counted_input = require_positive_integer(
        preflight.get("counted_input_tokens"),
        f"{label} budget_preflight.counted_input_tokens",
    )
    max_output = require_positive_integer(
        preflight.get("max_output_tokens"),
        f"{label} budget_preflight.max_output_tokens",
    )
    maximum_cost = require_nonnegative_number(
        preflight.get("maximum_token_cost_usd"),
        f"{label} budget_preflight.maximum_token_cost_usd",
    )
    model_usage = data.get("modelUsage") or {}
    if not isinstance(model_usage, dict) or len(model_usage) != 1:
        raise ValueError(f"{label}: API log must contain exactly one model")
    model = next(iter(model_usage))
    if model not in runner.API_INPUT_USD_PER_MTOK:
        raise ValueError(f"{label}: no reviewed price for {model}")
    if max_output != runner.API_MAX_OUTPUT_TOKENS:
        raise ValueError(f"{label}: API maximum output-token cap drifted")
    expected_maximum = round(
        counted_input * runner.API_INPUT_USD_PER_MTOK[model] / 1_000_000
        + max_output * runner.API_OUTPUT_USD_PER_MTOK[model] / 1_000_000,
        6,
    )
    if not math.isclose(maximum_cost, expected_maximum, abs_tol=1e-6):
        raise ValueError(f"{label}: API maximum token-cost calculation differs")
    if maximum_cost > budget + 1e-9:
        raise ValueError(f"{label}: API preflight maximum exceeds its budget")

    if preflight.get("pricing_source") != runner.API_PRICING_SOURCE:
        raise ValueError(f"{label}: API pricing source differs from current runner")
    if preflight.get("pricing_checked") != runner.API_PRICING_CHECKED:
        raise ValueError(f"{label}: API pricing review date differs from current runner")
    if preflight.get("billing_cutoff") is not False:
        raise ValueError(f"{label}: API preflight must not claim a billing cutoff")
    if provenance.get("budget_enforcement") != (
        "preflight token-cost ceiling; not an account billing cutoff"
    ):
        raise ValueError(f"{label}: API budget-enforcement description is inaccurate")

    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError(f"{label}: API usage is not an object")
    expected_actual = round(
        int(usage.get("input_tokens") or 0)
        * runner.API_INPUT_USD_PER_MTOK[model]
        / 1_000_000
        + int(usage.get("output_tokens") or 0)
        * runner.API_OUTPUT_USD_PER_MTOK[model]
        / 1_000_000
        + int(usage.get("cache_creation_input_tokens") or 0)
        * runner.API_INPUT_USD_PER_MTOK[model]
        * 1.25
        / 1_000_000
        + int(usage.get("cache_read_input_tokens") or 0)
        * runner.API_INPUT_USD_PER_MTOK[model]
        * 0.10
        / 1_000_000,
        6,
    )
    if not math.isclose(calculated_cost, expected_actual, abs_tol=1e-6):
        raise ValueError(f"{label}: API calculated cost differs from usage")
    if calculated_cost > budget + 1e-9:
        raise ValueError(f"{label}: API calculated cost exceeds its budget")
    provider_cost = data.get("total_cost_usd")
    if provider_cost is not None:
        provider_cost = require_nonnegative_number(
            provider_cost, f"{label} total_cost_usd"
        )

    return {
        "status": "api_token_cost_preflight_verified",
        "configured_budget_usd": budget,
        "budget_enforcement": (
            "deterministic token-cost ceiling; not an account billing cutoff"
        ),
        "provider_reported_cost_usd": provider_cost,
        "calculated_cost_usd": calculated_cost,
        "effective_cost_usd": calculated_cost,
        "cost_source": "calculated_from_api_usage_and_reviewed_rates",
        "budget_preflight": preflight,
        "limitation": (
            "The API check is a deterministic token-cost ceiling, not an "
            "Anthropic account billing cutoff."
        ),
    }


def validate_cli_accounting(
    data: dict[str, object],
    provenance: dict[str, object],
    *,
    label: str,
) -> dict[str, object]:
    budget = require_nonnegative_number(
        provenance.get("max_budget_usd"),
        f"{label} execution_provenance.max_budget_usd",
    )
    reported_cost = require_nonnegative_number(
        data.get("total_cost_usd"), f"{label} total_cost_usd"
    )
    if reported_cost > budget + 1e-9:
        raise ValueError(f"{label}: provider-reported cost exceeds CLI budget")
    budget_enforcement = provenance.get("budget_enforcement")
    if budget_enforcement not in {None, "Claude CLI hard agent budget"}:
        raise ValueError(f"{label}: unexpected CLI budget-enforcement description")
    if data.get("calculated_cost_usd") is not None:
        raise ValueError(f"{label}: CLI log unexpectedly contains calculated API cost")
    if data.get("budget_preflight") is not None:
        raise ValueError(f"{label}: CLI log unexpectedly contains API budget preflight")
    return {
        "status": "cli_agent_budget_verified",
        "configured_budget_usd": budget,
        "budget_enforcement": "Claude CLI hard agent budget",
        "provider_reported_cost_usd": reported_cost,
        "calculated_cost_usd": None,
        "effective_cost_usd": reported_cost,
        "cost_source": "provider_reported_cli_cost",
        "limitation": None,
    }


def validate_provider_log(
    path: Path,
    *,
    blind_root: Path,
    stage: str,
    module: str | None,
    output_path: Path,
    generated_prompt: str,
    strict_schema: dict[str, object],
) -> dict[str, object]:
    scope = module or "global"
    label = f"{scope} {stage}"
    data = load_json(path)
    if not isinstance(data, dict):
        raise TypeError(f"{label}: provider log is not an object")
    if data.get("is_error") is not False:
        raise ValueError(f"{label}: provider log is not a successful result")
    if data.get("terminal_reason") != "completed":
        raise ValueError(f"{label}: provider terminal reason was not completed")
    if data.get("permission_denials"):
        raise ValueError(f"{label}: permission denials present")
    server_use = (data.get("usage") or {}).get("server_tool_use") or {}
    if int(server_use.get("web_search_requests") or 0) != 0:
        raise ValueError(f"{label}: web search use present")
    if int(server_use.get("web_fetch_requests") or 0) != 0:
        raise ValueError(f"{label}: web fetch use present")
    models = set((data.get("modelUsage") or {}).keys())
    if not any("opus" in model for model in models):
        raise ValueError(f"{label}: no Opus model use recorded")

    provenance = data.get("execution_provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"{label}: execution provenance is missing")
    filename_transport = "api" if path.name.startswith("claude_api_") else "cli"
    provenance_transport = normalize_transport(
        provenance.get("transport") or filename_transport
    )
    if provenance_transport != filename_transport:
        raise ValueError(f"{label}: log filename and provenance transport disagree")
    if data.get("transport") is not None:
        if normalize_transport(data["transport"]) != filename_transport:
            raise ValueError(f"{label}: provider and provenance transports disagree")
    transport = filename_transport

    prompt_path = require_logged_file(
        provenance,
        "prompt_path",
        allowed_root=blind_root / "prompts",
        label=label,
    )
    prompt_hash = verify_logged_hash(
        provenance, "prompt_sha256", prompt_path, label=label
    )
    generated_bytes = generated_prompt.encode("utf-8")
    generated_hash = bytes_sha256(generated_bytes)
    if prompt_path.read_bytes() != generated_bytes:
        raise ValueError(
            f"{label}: logged prompt was not generated by the current frozen "
            f"template; logged={prompt_hash}, current={generated_hash}"
        )

    attempt = require_positive_integer(
        provenance.get("attempt"), f"{label} execution_provenance.attempt"
    )
    parsed_path = require_logged_file(
        provenance,
        "parsed_output_path",
        allowed_root=output_path.parent,
        expected=output_path,
        label=label,
    )
    parsed_hash = verify_logged_hash(
        provenance, "parsed_output_sha256", parsed_path, label=label
    )
    raw_path = require_logged_file(
        provenance,
        "raw_provider_output_path",
        allowed_root=output_path.parent,
        label=label,
    )
    expected_raw_name = (
        f"claude_{transport}_{stage}_attempt_{attempt:02d}_raw.json"
    )
    if raw_path.name != expected_raw_name:
        raise ValueError(
            f"{label}: raw path name is {raw_path.name}, "
            f"expected {expected_raw_name}"
        )
    raw_hash = verify_logged_hash(
        provenance, "raw_provider_output_sha256", raw_path, label=label
    )
    raw = load_json(raw_path)
    logged_without_provenance = {
        key: value
        for key, value in data.items()
        if key != "execution_provenance"
    }
    if raw != logged_without_provenance:
        raise ValueError(
            f"{label}: final log content differs from its bound raw response"
        )

    strict_schema_hash = canonical_json_sha256(strict_schema)
    if provenance.get("schema_sha256") != strict_schema_hash:
        raise ValueError(
            f"{label}: strict schema hash differs from the current runner"
        )
    provider_schema_hash: str | None = None
    provider_schema_relaxed = False
    if transport == "api":
        provider_schema = runner.api_compatible_schema(strict_schema)
        provider_schema_hash = canonical_json_sha256(provider_schema)
        if provenance.get("provider_schema_sha256") != provider_schema_hash:
            raise ValueError(
                f"{label}: API provider-schema hash differs from current runner"
            )
        provider_schema_relaxed = provider_schema_hash != strict_schema_hash
        note = provenance.get("provider_schema_note")
        if provider_schema_relaxed and (
            not isinstance(note, str) or API_SCHEMA_NOTE_FRAGMENT not in note
        ):
            raise ValueError(
                f"{label}: relaxed API provider schema is not truthfully noted"
            )
        accounting = validate_api_accounting(data, provenance, label=label)
    else:
        if provenance.get("provider_schema_sha256") is not None:
            raise ValueError(f"{label}: CLI log unexpectedly has provider schema")
        if provenance.get("provider_schema_note") is not None:
            raise ValueError(f"{label}: CLI log unexpectedly has provider-schema note")
        accounting = validate_cli_accounting(data, provenance, label=label)

    return {
        "scope": scope,
        "stage": stage,
        "transport": transport,
        "attempt": attempt,
        "models": sorted(models),
        "log_path": str(path.resolve()),
        "log_sha256": sha256(path),
        "prompt_path": str(prompt_path),
        "prompt_sha256": prompt_hash,
        "raw_provider_output_path": str(raw_path),
        "raw_provider_output_sha256": raw_hash,
        "parsed_output_path": str(parsed_path),
        "parsed_output_sha256": parsed_hash,
        "strict_schema_sha256": strict_schema_hash,
        "provider_schema_sha256": provider_schema_hash,
        "provider_schema_relaxed": provider_schema_relaxed,
        "accounting": accounting,
        "_paths": [path.resolve(), prompt_path, raw_path, parsed_path],
    }


def validate_module(blind_root: Path, module: str) -> dict[str, object]:
    if module not in MODULES:
        raise ValueError(f"unknown module {module!r}")
    module_code = MODULES[module]
    expected_claims = claim_ids_for(blind_root, module_code)
    expected_claim_set = set(expected_claims)
    module_dir = blind_root / "outputs" / "modules" / module
    screening_path = module_dir / "screening_ai_r2_claude.json"
    synthesis_path = module_dir / "synthesis_ai_r2_claude.json"
    if not screening_path.is_file():
        raise FileNotFoundError(screening_path)
    if not synthesis_path.is_file():
        raise FileNotFoundError(synthesis_path)

    screening = runner.validate_screening(
        load_json(screening_path), blind_root, module
    )
    for row in screening:
        if row["module"] != module:
            raise ValueError(f"{module}: screening ownership differs")
        if not set(row["claim_ids"]) <= expected_claim_set:
            raise ValueError(f"{module}: screening cites another module's claim")
    screening_log = find_provider_log(module_dir, "screen")
    screen_call = validate_provider_log(
        screening_log,
        blind_root=blind_root,
        stage="screen",
        module=module,
        output_path=screening_path,
        generated_prompt=runner.screening_prompt(blind_root, module),
        strict_schema=runner.screening_schema(blind_root, module),
    )

    synthesis = runner.validate_synthesis(
        load_json(synthesis_path), blind_root, module
    )
    assessment_ids = [
        str(row["claim_id"]) for row in synthesis["claim_assessments"]
    ]
    if assessment_ids != expected_claims:
        raise ValueError(
            f"{module}: claim assessments differ from module ownership/order"
        )
    for row in synthesis["extractions"]:
        if not set(row["claim_ids"]) <= expected_claim_set:
            raise ValueError(f"{module}: extraction cites another module's claim")
    synthesis_log = find_provider_log(module_dir, "synthesize")
    synthesis_call = validate_provider_log(
        synthesis_log,
        blind_root=blind_root,
        stage="synthesize",
        module=module,
        output_path=synthesis_path,
        generated_prompt=runner.synthesis_prompt(blind_root, module),
        strict_schema=runner.synthesis_schema(blind_root, module),
    )

    decisions = Counter(str(row["decision"]) for row in screening)
    return {
        "module": module,
        "module_code": module_code,
        "screening": dict(decisions),
        "extractions": len(synthesis["extractions"]),
        "claim_assessments": len(assessment_ids),
        "assessment_ids": assessment_ids,
        "provider_calls": [screen_call, synthesis_call],
    }


def validate_global_output(blind_root: Path) -> dict[str, object]:
    output_path = blind_root / "outputs" / "global_synthesis_ai_r2_claude.json"
    if not output_path.is_file():
        raise FileNotFoundError(output_path)
    output = runner.validate_global(load_json(output_path), blind_root)
    expected_claims = claim_ids_for(blind_root, "G")
    assessment_ids = [
        str(row["claim_id"]) for row in output["claim_assessments"]
    ]
    if assessment_ids != expected_claims:
        raise ValueError("global claim assessments differ from G ownership/order")
    log_path = find_provider_log(blind_root / "outputs", "global")
    provider_call = validate_provider_log(
        log_path,
        blind_root=blind_root,
        stage="global",
        module=None,
        output_path=output_path,
        generated_prompt=runner.global_prompt(blind_root),
        strict_schema=runner.global_schema(blind_root),
    )
    return {
        "assessment_ids": assessment_ids,
        "global_null_hypothesis": output["global_null_hypothesis"],
        "provider_call": provider_call,
    }


def public_provider_call(call: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in call.items() if key != "_paths"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-root", type=Path, default=DEFAULT_BLIND_ROOT)
    args = parser.parse_args()
    blind_root = args.blind_root.resolve()
    seal_path = blind_root / "provenance" / "claude_scientific_output_seal.json"
    if seal_path.exists():
        raise FileExistsError(f"refusing to overwrite existing seal: {seal_path}")

    runner_sha256 = sha256(RUNNER_PATH)
    expected_claims = all_expected_claim_ids(blind_root)
    all_assessments: list[str] = []
    total_screening: Counter[str] = Counter()
    selected_occurrences = 0
    module_summaries: dict[str, object] = {}
    provider_calls: list[dict[str, object]] = []
    final_paths: set[Path] = set()

    for module in MODULES:
        result = validate_module(blind_root, module)
        total_screening.update(result["screening"])
        selected_occurrences += int(result["extractions"])
        all_assessments.extend(result["assessment_ids"])
        calls = result["provider_calls"]
        provider_calls.extend(calls)
        for call in calls:
            final_paths.update(call["_paths"])
        module_summaries[module] = {
            "module_code": result["module_code"],
            "screening": result["screening"],
            "extractions": result["extractions"],
            "claim_assessments": result["claim_assessments"],
        }

    global_result = validate_global_output(blind_root)
    all_assessments.extend(global_result["assessment_ids"])
    global_call = global_result["provider_call"]
    provider_calls.append(global_call)
    final_paths.update(global_call["_paths"])

    if Counter(all_assessments) != Counter(expected_claims):
        raise ValueError(
            "Claude claim ownership/coverage differs from the frozen ledger"
        )
    expected_screened = 140 * len(MODULES)
    if sum(total_screening.values()) != expected_screened:
        raise ValueError(
            f"Claude screening coverage is not {expected_screened}"
        )
    if sha256(RUNNER_PATH) != runner_sha256:
        raise RuntimeError("runner changed while the seal was being validated")

    transports = {str(call["transport"]) for call in provider_calls}
    models = {
        model
        for call in provider_calls
        for model in call["models"]
    }
    known_costs = [
        float(call["accounting"]["effective_cost_usd"])
        for call in provider_calls
        if call["accounting"]["effective_cost_usd"] is not None
    ]
    cost_sources = Counter(
        str(call["accounting"]["cost_source"])
        for call in provider_calls
        if call["accounting"]["cost_source"] is not None
    )
    budget_statuses = Counter(
        str(call["accounting"]["status"]) for call in provider_calls
    )
    limitations = sorted(
        {
            str(call["accounting"]["limitation"])
            for call in provider_calls
            if call["accounting"]["limitation"]
        }
    )

    cli_version = None
    if "cli" in transports:
        cli_version = subprocess.run(
            [runner.CLAUDE_COMMAND, "--version"],
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    manifest_path = blind_root / "provenance" / "blind_input_manifest.json"
    final_paths.add(manifest_path)
    events_path = blind_root / "provenance" / "execution_events.json"
    if events_path.is_file():
        final_paths.add(events_path)
    seal = {
        "status": (
            "Claude blinded scientific judgments validated and sealed before "
            "Codex comparison"
        ),
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "secondary AI reviewer; not Human Reviewer 2 or clinician",
        "blind_input_manifest_sha256": sha256(manifest_path),
        "runner": {
            "path": str(RUNNER_PATH),
            "sha256": runner_sha256,
            "current_templates_and_validators_replayed": True,
        },
        "claude_cli_version_observed_at_seal_time": cli_version,
        "models_observed": sorted(models),
        "transports_observed": sorted(transports),
        "provider_call_accounting": {
            "successful_final_calls": len(provider_calls),
            "known_cost_usd_sum": round(sum(known_costs), 6),
            "calls_with_known_cost": len(known_costs),
            "calls_without_known_cost": len(provider_calls) - len(known_costs),
            "cost_source_counts": dict(cost_sources),
            "budget_status_counts": dict(budget_statuses),
            "limitations": limitations,
        },
        "provider_calls": [
            public_provider_call(call) for call in provider_calls
        ],
        "screening": dict(total_screening),
        "selected_extraction_occurrences": selected_occurrences,
        "claim_assessments": len(all_assessments),
        "global_null_hypothesis": global_result["global_null_hypothesis"],
        "module_summary": module_summaries,
        "files": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in sorted(final_paths, key=str)
        ],
        "gate_status": {
            "secondary_ai_reviewer_claude": "complete",
            "ai_ai_reconciliation": "not started",
            "human_reviewer_2": "pending",
            "full_text_screening": "pending",
            "formal_risk_of_bias": "pending",
            "clinical_sign_off": "pending",
            "official_engine_export": "withheld",
        },
    }
    seal_path.write_text(
        json.dumps(seal, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "seal": str(seal_path),
                "screened": sum(total_screening.values()),
                "extracted_occurrences": selected_occurrences,
                "claim_assessments": len(all_assessments),
                "models": sorted(models),
                "transports": sorted(transports),
                "known_cost_usd": seal["provider_call_accounting"][
                    "known_cost_usd_sum"
                ],
                "calls_without_known_cost": seal[
                    "provider_call_accounting"
                ]["calls_without_known_cost"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
