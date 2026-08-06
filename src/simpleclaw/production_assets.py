"""Version-controlled production asset templates and installers."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from simpleclaw.config_sections.agents import (
    _RECIPES_DEFAULTS,
    load_recipes_config,
)

NAVER_SPORTS_SKILL_NAME = "naver-sports-skill"
SPORTS_LIVE_RECIPE_NAME = "sports-live"
DEFAULT_GLOBAL_SKILLS_DIR = Path("~/.agents/skills")
DEFAULT_RECIPES_DIR = Path(_RECIPES_DEFAULTS["dir"])
DEFAULT_CONFIG_PATH = Path("~/.simpleclaw/config.yaml")
_RUNTIME_ASSETS = files("simpleclaw").joinpath("runtime_assets")
CANONICAL_NAVER_SPORTS_SKILL_MD = (
    _RUNTIME_ASSETS.joinpath(
        "skills", NAVER_SPORTS_SKILL_NAME, "SKILL.md"
    )
)
CANONICAL_SPORTS_LIVE_RECIPE = (
    _RUNTIME_ASSETS.joinpath(
        "recipes", SPORTS_LIVE_RECIPE_NAME, "recipe.yaml"
    )
)
NAVER_SPORTS_WRAPPER = """#!/usr/bin/env python3
from simpleclaw.skills.naver_sports import main

if __name__ == "__main__":
    raise SystemExit(main())
"""


def install_naver_sports_skill(
    global_dir: Path | None = None,
) -> Path:
    """Canonical Naver Sports metadata와 wrapper를 설치한다."""
    if global_dir is None:
        global_dir = DEFAULT_GLOBAL_SKILLS_DIR.expanduser()
    skill_dir = global_dir / NAVER_SPORTS_SKILL_NAME
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_bytes(
        CANONICAL_NAVER_SPORTS_SKILL_MD.read_bytes()
    )
    wrapper = scripts_dir / "naver_sports.py"
    wrapper.write_text(NAVER_SPORTS_WRAPPER, encoding="utf-8")
    wrapper.chmod(0o755)
    return skill_dir


def install_sports_live_recipe(
    recipes_dir: Path | None = None,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Path:
    """Canonical sports-live recipe를 configured ``recipes.dir``에 설치한다."""
    if recipes_dir is None:
        configured = load_recipes_config(config_path.expanduser())["dir"]
        recipes_dir = Path(configured).expanduser()
    recipe_dir = recipes_dir / SPORTS_LIVE_RECIPE_NAME
    recipe_dir.mkdir(parents=True, exist_ok=True)
    (recipe_dir / "recipe.yaml").write_bytes(
        CANONICAL_SPORTS_LIVE_RECIPE.read_bytes()
    )
    return recipe_dir
