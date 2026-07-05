"""Live equity research recipe guardrail tests.

SimpleClaw의 운영 recipe는 사용자 홈의 live asset 디렉터리에 존재하므로,
CI처럼 해당 디렉터리가 없는 환경에서는 skip하고 운영 머신에서는 실제 YAML을
검증한다. 레시피 변경은 prompt-only라도 숫자 조작 위험이 커서 discovery와
렌더링 guardrail을 회귀 테스트로 묶어 둔다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simpleclaw.recipes.executor import render_instructions
from simpleclaw.recipes.loader import discover_recipes

LIVE_RECIPE_ROOT = Path("/Users/simplist/.simpleclaw-agent/default/recipes")
EQUITY_RECIPES = {"tearsheet", "bullbear", "earnings"}
ACCESSIBLE_SKILLS = {
    "us-stock-skill",
    "kr-stock-skill",
    "news-search-skill",
    "google-news-search-skill",
}


def _require_live_recipe_root() -> Path:
    """운영 recipe 디렉터리가 없는 CI/개발 환경에서는 live asset 테스트를 건너뛴다."""
    if not LIVE_RECIPE_ROOT.is_dir():
        pytest.skip(f"live recipe root not present: {LIVE_RECIPE_ROOT}")
    return LIVE_RECIPE_ROOT


def _load_recipe_yaml(name: str) -> dict[str, object]:
    """loader가 보존하지 않는 trigger/skills까지 검증하기 위해 원본 YAML도 읽는다."""
    recipe_path = _require_live_recipe_root() / name / "recipe.yaml"
    if not recipe_path.is_file():
        pytest.fail(f"missing live recipe file: {recipe_path}")
    data = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _rendered_recipe(name: str, keyword: str) -> str:
    recipes = {recipe.name: recipe for recipe in discover_recipes(_require_live_recipe_root())}
    recipe = recipes[name]
    return render_instructions(recipe.instructions, variables={"keyword": keyword})


def test_equity_research_recipes_discoverable():
    recipes = {recipe.name: recipe for recipe in discover_recipes(_require_live_recipe_root())}

    assert EQUITY_RECIPES <= set(recipes)
    for name in EQUITY_RECIPES:
        recipe = recipes[name]
        raw = _load_recipe_yaml(name)
        assert recipe.description
        raw_skills = raw.get("skills")
        assert raw.get("trigger")
        assert isinstance(raw_skills, list)
        assert set(raw_skills) <= ACCESSIBLE_SKILLS
        assert set(raw_skills)


def test_tearsheet_recipe_render_empty_and_symbol_keyword():
    empty_render = _rendered_recipe("tearsheet", "")
    symbol_render = _rendered_recipe("tearsheet", "AAPL")

    for rendered in (empty_render, symbol_render):
        assert "source of truth" in rendered
        assert "데이터 기준일" in rendered
        assert "데이터 미확보" in rendered
        assert "투자 조언이 아니라" in rendered
        assert "숫자를 임의 생성" in rendered
    assert "AAPL" in symbol_render


def test_bullbear_recipe_requires_evidence_backed_sections():
    rendered = _rendered_recipe("bullbear", "NVDA")

    assert "NVDA" in rendered
    assert "Bull case" in rendered
    assert "Bear case" in rendered
    assert "확인할 정량 지표" in rendered
    assert "반증 조건" in rendered
    assert "뉴스 날짜" in rendered
    assert "구조화 수치" in rendered


def test_earnings_recipe_has_no_fabrication_guardrails():
    rendered = _rendered_recipe("earnings", "MSFT")

    assert "MSFT" in rendered
    assert "실적 발표" in rendered
    assert "가이던스" in rendered
    assert "컨센서스" in rendered
    assert "밸류에이션" in rendered
    assert "임의 생성 금지" in rendered
    assert "데이터 미확보" in rendered
