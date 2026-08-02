from decimal import Decimal

import pytest

from simpleclaw.config import load_llm_usage_config
from simpleclaw.llm.models import LLMConfigError


def test_usage_config_defaults_disabled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm: {}\n", encoding="utf-8")
    loaded = load_llm_usage_config(path)
    assert loaded["enabled"] is False
    assert loaded["timezone"] == "Asia/Seoul"


def test_usage_config_normalizes_decimal_rates(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  usage:\n    pricing:\n      primary:\n        version: v1\n        input_per_million_usd: '3.25'\n", encoding="utf-8")
    assert load_llm_usage_config(path)["pricing"]["primary"]["input_per_million_usd"] == Decimal("3.25")


def test_usage_config_rejects_invalid_timezone(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  usage:\n    timezone: Mars/Base\n", encoding="utf-8")
    with pytest.raises(LLMConfigError, match="timezone"):
        load_llm_usage_config(path)


@pytest.mark.parametrize("value", ["NaN", "Infinity", -1, True])
def test_usage_config_rejects_invalid_rates(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"llm:\n  usage:\n    pricing:\n      primary:\n        input_per_million_usd: {value}\n", encoding="utf-8")
    with pytest.raises(LLMConfigError):
        load_llm_usage_config(path)
