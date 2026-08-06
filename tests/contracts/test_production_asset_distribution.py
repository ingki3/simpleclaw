"""BIZ-611 — distribution artifact의 canonical production asset 회귀."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tarfile import open as open_tar
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).parents[2]
EXPECTED_ASSETS = {
    "simpleclaw/runtime_assets/recipes/sports-live/recipe.yaml",
    "simpleclaw/runtime_assets/recipes/sports-live/runtime-asset.yaml",
    "simpleclaw/runtime_assets/skills/naver-sports-skill/SKILL.md",
    "simpleclaw/runtime_assets/skills/naver-sports-skill/runtime-asset.yaml",
    "simpleclaw/runtime_assets/skills/naver-sports-skill/scripts/naver_sports.py",
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

    isolated = tmp_path / "isolated"
    isolated.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path;"
                "from simpleclaw.production_assets import install_runtime_asset;"
                "path,resolved=install_runtime_asset("
                "'recipe:sports-live',destination_parent=Path(__import__('sys').argv[1]));"
                "print(path);print(resolved.provenance)"
            ),
            str(isolated),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(wheel_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "package:simpleclaw/runtime_assets" in completed.stdout
    assert (isolated / "sports-live/recipe.yaml").read_bytes() == (
        ROOT / "runtime_assets/recipes/sports-live/recipe.yaml"
    ).read_bytes()


def test_built_sdist_contains_authoring_asset_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("setuptools.build_meta")
    from setuptools.build_meta import build_sdist

    monkeypatch.chdir(ROOT)
    sdist_path = tmp_path / build_sdist(str(tmp_path))
    with open_tar(sdist_path) as archive:
        names = {name.partition("/")[2] for name in archive.getnames()}
    assert {
        "runtime_assets/recipes/sports-live/runtime-asset.yaml",
        "runtime_assets/skills/naver-sports-skill/runtime-asset.yaml",
    } <= names
