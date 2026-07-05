"""Recipe runtime entrypoint contracts.

`agent-study-daily` 사고처럼 recipe.yaml shape와 runtime 실행 경로가 서로
어긋나는 문제를 PR 단계에서 잡기 위한 contract layer다.
"""

from __future__ import annotations

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.agent.commands import try_recipe_command
from simpleclaw.daemon.models import ActionType, CronJob
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.recipes.executor import execute_recipe
from simpleclaw.recipes.loader import discover_recipes, load_recipe
from simpleclaw.recipes.models import StepType


def test_steps_recipe_preserves_settings_timeout(recipe_contract_dir):
    """loader는 command recipe의 settings.timeout을 보존해야 한다."""
    recipe_path = recipe_contract_dir / "agent-study-daily" / "recipe.yaml"

    recipe = load_recipe(recipe_path)

    assert recipe.name == "agent-study-daily"
    assert recipe.instructions == ""
    assert recipe.settings.timeout == 180
    assert len(recipe.steps) == 1
    assert recipe.steps[0].step_type is StepType.COMMAND


def test_discover_recipes_finds_command_recipe(recipe_contract_dir):
    """discover_recipes는 live recipes.dir shape의 command recipe를 찾는다."""
    recipes = discover_recipes(recipe_contract_dir)

    assert [recipe.name for recipe in recipes] == ["agent-study-daily"]
    assert recipes[0].settings.timeout == 180


@pytest.mark.asyncio
async def test_execute_recipe_accepts_recipe_specific_timeout(recipe_contract_dir):
    """executor 호출자는 recipe.settings.timeout을 전달할 수 있어야 한다."""
    recipe = load_recipe(recipe_contract_dir / "agent-study-daily" / "recipe.yaml")

    result = await execute_recipe(recipe, timeout=recipe.settings.timeout)

    assert result.success is True
    assert result.step_results[0].output.strip() == "study ok"


@pytest.mark.asyncio
async def test_slash_recipe_command_executes_steps_recipe_without_instructions(
    recipe_contract_dir,
):
    """수동 `/recipe` 경로는 instructions 없는 v1 steps recipe를 실행한다."""

    async def fake_react_loop(prompt: str, **kwargs):
        return f"LLM:{prompt}"

    outcome = await try_recipe_command(
        "/agent-study-daily",
        fake_react_loop,
        recipes_dir=recipe_contract_dir,
        legacy_recipes_dir=None,
    )

    assert outcome is not None
    response, recipe_name = outcome
    assert recipe_name == "agent-study-daily"
    assert "study ok" in response
    assert "instructions" not in response.lower()


class _FakeAgent:
    """CronScheduler가 호출할 최소 Agent double."""

    def __init__(self):
        self.prompts: list[str] = []
        self._command_guard = None

    async def process_cron_message(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"LLM:{prompt}"


@pytest.mark.asyncio
async def test_cron_recipe_action_executes_steps_recipe(recipe_contract_dir, tmp_path):
    """cron recipe action도 slash와 같은 steps recipe semantics를 사용한다."""
    agent = _FakeAgent()
    store = DaemonStore(tmp_path / "daemon.db")
    scheduler = CronScheduler(
        store=store,
        apscheduler=AsyncIOScheduler(),
        agent_orchestrator=agent,
        recipes_dir=recipe_contract_dir,
        legacy_recipes_dir=None,
    )
    job = CronJob(
        name="agent-study-daily",
        cron_expression="0 6 * * *",
        action_type=ActionType.RECIPE,
        action_reference="agent-study-daily",
    )

    try:
        output = await scheduler._execute_action(job)
    finally:
        store.close()

    assert output == "LLM:study ok"
    assert agent.prompts == ["study ok"]
