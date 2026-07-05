"""Runtime contract test fixtures.

외부 네트워크, live Telegram, live runtime DB를 건드리지 않고 repo fixture만
사용한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def recipe_contract_dir(tmp_path: Path) -> Path:
    """Create a live-shape command recipe fixture with recipe settings."""
    recipes = tmp_path / "recipes"
    daily = recipes / "agent-study-daily"
    daily.mkdir(parents=True)
    (daily / "recipe.yaml").write_text(
        """
name: agent-study-daily
description: Contract fixture for command based daily study recipe.
settings:
  timeout: 180
steps:
  - type: command
    name: dry-run-study
    content: python -c "print('study ok')"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return recipes
