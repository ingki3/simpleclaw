"""Recipe-owned natural-language argument constraint 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.recipes.bindings import (
    constraint_values,
    resolve_recipe_argument_constraints,
)
from simpleclaw.recipes.loader import load_recipe

FIXTURE = Path(__file__).parents[1] / "fixtures" / "recipes" / "sports-live"


def _values(query: str) -> dict[str, int]:
    recipe = load_recipe(FIXTURE / "recipe.yaml")
    return constraint_values(
        resolve_recipe_argument_constraints(recipe, {"query": query})
    )


@pytest.mark.parametrize(
    "query",
    (
        "현재 KBO 순위 상위 3팀만 알려줘",
        "현재 KBO 순위 3팀만 알려줘",
        "Show the top 3 teams in KBO",
    ),
)
def test_explicit_top_n_is_preserved_as_bound_limit(query: str) -> None:
    assert _values(query) == {"limit": 3}


def test_missing_top_n_uses_asset_owned_default() -> None:
    assert _values("현재 KBO 순위를 알려줘") == {"limit": 10}


@pytest.mark.parametrize(
    "query",
    (
        "현재 KBO 순위 상위 0팀만 알려줘",
        "현재 KBO 순위 상위 21팀만 알려줘",
        "현재 KBO 순위 상위 3팀과 top 5 teams를 알려줘",
    ),
)
def test_invalid_or_ambiguous_top_n_fails_closed(query: str) -> None:
    recipe = load_recipe(FIXTURE / "recipe.yaml")

    with pytest.raises(ValueError):
        resolve_recipe_argument_constraints(recipe, {"query": query})
