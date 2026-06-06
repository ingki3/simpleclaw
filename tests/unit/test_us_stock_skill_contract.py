"""Live us-stock-skill CLI contract tests for BIZ-354."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from simpleclaw.recipes.loader import load_recipe


SKILL_DIR = Path("/Users/simplist/.agents/skills/us-stock-skill")
SCRIPT = SKILL_DIR / "scripts" / "us_stock.py"
PYTHON = SKILL_DIR / "scripts" / "venv" / "bin" / "python"
USSTOCK_RECIPE = Path("/Users/simplist/.simpleclaw-agent/default/recipes/usstock/recipe.yaml")


def _skip_if_live_skill_missing() -> None:
    if not SCRIPT.is_file() or not PYTHON.is_file():
        pytest.skip("live us-stock-skill is not installed on this machine")


def _run_json(command: str) -> dict:
    _skip_if_live_skill_missing()
    proc = subprocess.run(
        [str(PYTHON), str(SCRIPT), command, "--symbol", "AAPL", "--json"],
        check=True,
        text=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads(proc.stdout)


def _assert_sourced_value_shape(node: dict, *, allow_unavailable: bool = True) -> None:
    assert {"value", "status", "source", "freshness"}.issubset(node)
    assert node["status"] in {"available", "calculated", "unavailable"}
    if node["status"] == "unavailable":
        assert allow_unavailable
        assert node["value"] is None
        assert node.get("reason")
        return
    assert node["value"] is not None
    assert node["source"]


def test_us_stock_cli_fundamentals_json_contract() -> None:
    data = _run_json("fundamentals")

    assert data["symbol"] == "AAPL"
    assert {"profile", "latest_price", "financials", "ratios", "source", "freshness", "unavailable"}.issubset(data)
    assert data["profile"]["symbol"] == "AAPL"
    _assert_sourced_value_shape(data["latest_price"], allow_unavailable=False)

    for key in ["revenue", "net_income", "ebitda", "free_cash_flow", "total_debt", "cash"]:
        _assert_sourced_value_shape(data["financials"][key])
    for key in ["gross_margin", "operating_margin", "profit_margin", "debt_to_equity", "return_on_equity"]:
        _assert_sourced_value_shape(data["ratios"][key])

    assert isinstance(data["unavailable"], list)
    assert all("field" in item and "reason" in item for item in data["unavailable"])


def test_us_stock_cli_earnings_json_contract() -> None:
    data = _run_json("earnings")

    assert data["symbol"] == "AAPL"
    assert {"recent_results", "earnings_dates", "guidance", "consensus", "source", "freshness", "unavailable"}.issubset(data)
    assert isinstance(data["recent_results"], list)
    for item in data["recent_results"][:2]:
        assert {"period", "revenue", "earnings", "source", "freshness"}.issubset(item)
    assert isinstance(data["earnings_dates"], list)
    _assert_sourced_value_shape(data["guidance"]["revenue"])
    _assert_sourced_value_shape(data["guidance"]["eps"])
    _assert_sourced_value_shape(data["consensus"]["eps_estimate"])
    assert isinstance(data["unavailable"], list)


def test_us_stock_cli_valuation_json_contract() -> None:
    data = _run_json("valuation")

    assert data["symbol"] == "AAPL"
    assert {"latest_price", "market_cap", "multiples", "source", "freshness", "unavailable"}.issubset(data)
    _assert_sourced_value_shape(data["latest_price"], allow_unavailable=False)
    _assert_sourced_value_shape(data["market_cap"])
    for key in ["trailing_pe", "forward_pe", "price_to_sales", "price_to_book", "ev_to_ebitda"]:
        _assert_sourced_value_shape(data["multiples"][key])
    assert isinstance(data["unavailable"], list)


def test_usstock_recipe_prefers_structured_commands() -> None:
    if not USSTOCK_RECIPE.is_file():
        pytest.skip("live usstock recipe is not installed on this machine")

    recipe = load_recipe(USSTOCK_RECIPE)
    instructions = recipe.instructions

    assert "fundamentals --symbol" in instructions
    assert "earnings --symbol" in instructions
    assert "valuation --symbol" in instructions
    assert "구조화" in instructions
    assert "데이터 미확인" in instructions or "unavailable" in instructions
    assert "임의" in instructions
