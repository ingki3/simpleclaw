"""BIZ-611 — distribution artifact의 canonical production asset 회귀."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).parents[2]
EXPECTED_ASSETS = {
    "simpleclaw/runtime_assets/recipes/sports-live/recipe.yaml",
    "simpleclaw/runtime_assets/skills/naver-sports-skill/SKILL.md",
}


def test_built_wheel_contains_canonical_production_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip(
        "setuptools.build_meta",
        reason="wheel build backend is not installed in this test environment",
    )
    from setuptools.build_meta import build_wheel

    monkeypatch.chdir(ROOT)
    wheel_path = tmp_path / build_wheel(str(tmp_path))
    with ZipFile(wheel_path) as wheel:
        assert EXPECTED_ASSETS <= set(wheel.namelist())
