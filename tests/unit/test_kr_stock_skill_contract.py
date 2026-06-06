"""kr-stock-skill live CLI 계약 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


SKILL_ROOT = Path("/Users/simplist/.agents/skills/kr-stock-skill")
PYTHON = SKILL_ROOT / "scripts" / "venv" / "bin" / "python"
SCRIPT = SKILL_ROOT / "scripts" / "kr_stock.py"


def _require_live_cli() -> None:
    """CI 등 live skill이 없는 환경에서는 명시적으로 건너뛴다."""
    if not PYTHON.exists() or not SCRIPT.exists():
        pytest.skip(f"live kr-stock-skill CLI not present: {SCRIPT}")


def _run_json(*args: str) -> dict:
    """CLI JSON 출력을 파싱해 계약 테스트가 stderr에 흔들리지 않게 한다."""
    _require_live_cli()
    result = subprocess.run(
        [str(PYTHON), str(SCRIPT), *args, "--json"],
        check=True,
        text=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads(result.stdout)


def test_kr_stock_cli_market_summary_json_shape() -> None:
    """market-summary는 확보/미확보를 모두 구조화 JSON으로 표현해야 한다."""
    payload = _run_json("market-summary")

    assert payload["provider"] == "kr-stock-skill"
    assert payload["base_date"]
    assert set(payload) >= {"indices", "fx", "breadth", "sector", "flow", "turnover"}

    assert set(payload["indices"]) >= {"KS11", "KQ11"}
    assert payload["fx"]["USD/KRW"]["source"] == "FinanceDataReader"

    for key in ["breadth", "sector", "flow", "turnover"]:
        section = payload[key]
        assert section["status"] in {"available", "partial", "unavailable"}
        assert "source" in section
        if section["status"] == "unavailable":
            assert section["reason"]


def test_kr_stock_cli_preserves_fdr_quote_source() -> None:
    """기존 quote JSON 계약은 FinanceDataReader source와 rows를 유지해야 한다."""
    payload = _run_json("quote", "--symbol", "KS11")

    assert payload["provider"] == "FinanceDataReader"
    assert payload["requested_symbol"] == "KS11"
    assert payload["resolved_symbol"] == "KS11"
    assert isinstance(payload["rows"], list)
