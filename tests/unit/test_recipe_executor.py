"""Tests for the recipe executor."""

import pytest

from simpleclaw.recipes.executor import execute_recipe
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.recipes.models import (
    RecipeDefinition,
    RecipeExecutionError,
    RecipeStep,
    StepType,
)

from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "recipes"


class TestRecipeExecutor:
    @pytest.mark.asyncio
    async def test_full_recipe_execution(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        result = await execute_recipe(
            recipe, variables={"date": "2026-04-17"}
        )
        assert result.success
        assert len(result.step_results) == 2
        # Command step should have executed echo
        assert "2026-04-17" in result.step_results[0].output
        # Prompt step should have substituted variable
        assert "2026-04-17" in result.step_results[1].output

    @pytest.mark.asyncio
    async def test_variable_substitution(self):
        recipe = RecipeDefinition(
            name="test",
            steps=[
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="greet",
                    content="Hello ${name}, today is ${day}.",
                )
            ],
        )
        result = await execute_recipe(
            recipe, variables={"name": "Alice", "day": "Monday"}
        )
        assert result.success
        assert "Hello Alice" in result.step_results[0].output
        assert "Monday" in result.step_results[0].output

    @pytest.mark.asyncio
    async def test_default_parameter_applied(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        result = await execute_recipe(
            recipe, variables={"date": "2026-04-17"}
        )
        assert result.success
        # format default is "markdown"
        assert "markdown" in result.step_results[1].output

    @pytest.mark.asyncio
    async def test_missing_required_parameter_raises(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        with pytest.raises(RecipeExecutionError, match="Required parameter"):
            await execute_recipe(recipe, variables={})

    @pytest.mark.asyncio
    async def test_command_failure_stops_execution(self):
        recipe = RecipeDefinition(
            name="fail-test",
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="fail-step",
                    content="exit 1",
                ),
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="never-reached",
                    content="Should not run",
                ),
            ],
        )
        result = await execute_recipe(recipe)
        assert not result.success
        assert result.failed_step == "fail-step"
        assert len(result.step_results) == 1  # second step not executed

    @pytest.mark.asyncio
    async def test_empty_steps_succeeds(self):
        recipe = RecipeDefinition(name="empty")
        result = await execute_recipe(recipe)
        assert result.success
        assert len(result.step_results) == 0

    @pytest.mark.asyncio
    async def test_timeout_records_metrics(self):
        """COMMAND 스텝이 타임아웃될 때 ``metrics``로 종료 결과가 보고된다."""
        from simpleclaw.logging.metrics import MetricsCollector

        recipe = RecipeDefinition(
            name="slow",
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="slow-step",
                    content="sleep 10",
                ),
            ],
        )
        metrics = MetricsCollector()
        result = await execute_recipe(recipe, timeout=1, metrics=metrics)

        assert not result.success
        snap = metrics.get_snapshot()
        # SIGTERM 또는 SIGKILL 둘 중 하나가 카운트되어야 한다.
        assert snap.process_kills_sigterm + snap.process_kills_sigkill == 1
        assert snap.process_group_leaks == 0
