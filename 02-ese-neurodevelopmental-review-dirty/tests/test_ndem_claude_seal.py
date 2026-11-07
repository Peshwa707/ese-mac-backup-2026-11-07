"""Offline tests for the blinded Claude scientific-output seal."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "review_inputs"
    / "neurodevelopmental-environmental-mismatch"
)
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "ndem_claude_seal",
    SCRIPT_DIR / "seal_claude_secondary_review.py",
)
assert SPEC and SPEC.loader
seal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seal)


def _verified_api_accounting() -> tuple[dict[str, object], dict[str, object]]:
    model = "claude-opus-5"
    counted_input = 150_000
    output_tokens = 18_000
    budget = 5.0
    maximum_cost = round(
        counted_input * seal.runner.API_INPUT_USD_PER_MTOK[model] / 1_000_000
        + seal.runner.API_MAX_OUTPUT_TOKENS
        * seal.runner.API_OUTPUT_USD_PER_MTOK[model]
        / 1_000_000,
        6,
    )
    actual_cost = round(
        counted_input * seal.runner.API_INPUT_USD_PER_MTOK[model] / 1_000_000
        + output_tokens
        * seal.runner.API_OUTPUT_USD_PER_MTOK[model]
        / 1_000_000,
        6,
    )
    data: dict[str, object] = {
        "modelUsage": {model: {}},
        "usage": {
            "input_tokens": counted_input,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "total_cost_usd": None,
        "calculated_cost_usd": actual_cost,
        "budget_preflight": {
            "budget_usd": budget,
            "counted_input_tokens": counted_input,
            "max_output_tokens": seal.runner.API_MAX_OUTPUT_TOKENS,
            "maximum_token_cost_usd": maximum_cost,
            "pricing_source": seal.runner.API_PRICING_SOURCE,
            "pricing_checked": seal.runner.API_PRICING_CHECKED,
            "billing_cutoff": False,
        },
    }
    provenance: dict[str, object] = {
        "max_budget_usd": budget,
        "budget_enforcement": (
            "preflight token-cost ceiling; not an account billing cutoff"
        ),
    }
    return data, provenance


def test_verified_api_accounting_recomputes_cost_and_budget() -> None:
    data, provenance = _verified_api_accounting()

    result = seal.validate_api_accounting(
        data, provenance, label="test API"
    )

    assert result["status"] == "api_token_cost_preflight_verified"
    assert result["configured_budget_usd"] == 5.0
    assert result["effective_cost_usd"] == data["calculated_cost_usd"]
    assert "not an Anthropic account billing cutoff" in result["limitation"]


@pytest.mark.parametrize("missing", ["calculated_cost_usd", "budget_preflight"])
def test_partial_api_accounting_is_rejected(missing: str) -> None:
    data, provenance = _verified_api_accounting()
    data.pop(missing)

    with pytest.raises(ValueError, match="must appear together"):
        seal.validate_api_accounting(data, provenance, label="test API")


def test_legacy_api_accounting_never_implies_zero_or_budget_cap() -> None:
    result = seal.validate_api_accounting(
        {"calculated_cost_usd": None, "budget_preflight": None},
        {"max_budget_usd": None},
        label="legacy API",
    )

    assert result["status"] == "legacy_api_budget_and_cost_unverified"
    assert result["effective_cost_usd"] is None
    assert "$0" in result["limitation"]
    assert "verified $5 cap" in result["limitation"]


def test_final_provider_log_requires_exactly_one_transport(
    tmp_path: Path,
) -> None:
    cli = tmp_path / "claude_cli_screen.json"
    api = tmp_path / "claude_api_screen.json"
    cli.write_text("{}\n", encoding="utf-8")

    assert seal.find_provider_log(tmp_path, "screen") == cli
    api.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        seal.find_provider_log(tmp_path, "screen")
