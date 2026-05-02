"""Tests for the recipe executor."""

import pytest

from simpleclaw.recipes.executor import execute_recipe
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.recipes.models import (
    OnErrorPolicy,
    RecipeDefinition,
    RecipeExecutionError,
    RecipeStep,
    StepStatus,
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
        # ABORT 정책: 후속 스텝은 SKIPPED로 기록된다.
        assert len(result.step_results) == 2
        assert result.step_results[0].status == StepStatus.FAILED
        assert result.step_results[1].status == StepStatus.SKIPPED
        assert result.resumable_from == "fail-step"

    @pytest.mark.asyncio
    async def test_empty_steps_succeeds(self):
        recipe = RecipeDefinition(name="empty")
        result = await execute_recipe(recipe)
        assert result.success
        assert len(result.step_results) == 0

    @pytest.mark.asyncio
    async def test_continue_on_error_runs_all_steps(self):
        """on_error=continue 정책에서는 실패해도 후속 스텝이 실행된다."""
        recipe = RecipeDefinition(
            name="continue-test",
            on_error=OnErrorPolicy.CONTINUE,
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="ok-1",
                    content="echo first",
                ),
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="boom",
                    content="exit 7",
                ),
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="ok-2",
                    content="last",
                ),
            ],
        )
        result = await execute_recipe(recipe)
        assert not result.success
        assert result.failed_step == "boom"
        assert result.failed_steps == ["boom"]
        assert [s.status for s in result.step_results] == [
            StepStatus.SUCCESS,
            StepStatus.FAILED,
            StepStatus.SUCCESS,
        ]
        # 사용자 노출용 요약과 디버그 로그가 분리되어야 한다.
        assert result.error_summary
        assert "boom" in result.error_summary
        assert result.debug_log
        assert "exit=7" in result.debug_log

    @pytest.mark.asyncio
    async def test_per_step_on_error_overrides_recipe_policy(self):
        """스텝의 on_error 가 레시피 기본 정책보다 우선한다."""
        recipe = RecipeDefinition(
            name="per-step-override",
            on_error=OnErrorPolicy.ABORT,
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="tolerated",
                    content="exit 3",
                    on_error=OnErrorPolicy.CONTINUE,  # 이 스텝만 계속
                ),
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="reached",
                    content="hello",
                ),
            ],
        )
        result = await execute_recipe(recipe)
        assert not result.success
        assert result.step_results[1].status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_rollback_runs_in_reverse_for_succeeded_steps(self, tmp_path):
        """ROLLBACK 정책 시 성공 스텝의 rollback 명령이 역순 실행된다."""
        marker = tmp_path / "rollback.log"
        recipe = RecipeDefinition(
            name="rollback-test",
            on_error=OnErrorPolicy.ROLLBACK,
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="step-A",
                    content=f"echo A >> {marker}",
                    rollback=f"echo undo-A >> {marker}",
                ),
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="step-B",
                    content=f"echo B >> {marker}",
                    rollback=f"echo undo-B >> {marker}",
                ),
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="step-fail",
                    content="exit 1",
                    rollback="echo should-not-run",
                ),
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="step-D",
                    content="echo never",
                ),
            ],
        )
        result = await execute_recipe(recipe)
        assert not result.success
        assert result.failed_step == "step-fail"
        # 후속 스텝은 SKIPPED
        assert result.step_results[3].status == StepStatus.SKIPPED
        # 롤백은 step-B → step-A 순서, 실패한 스텝은 롤백 대상 아님
        assert len(result.rollback_results) == 2
        assert result.rollback_results[0].step_name == "rollback:step-B"
        assert result.rollback_results[1].step_name == "rollback:step-A"
        # 마커 파일에 실제로 롤백이 기록되었는지 확인
        contents = marker.read_text().splitlines()
        assert contents == ["A", "B", "undo-B", "undo-A"]

    @pytest.mark.asyncio
    async def test_resume_from_skips_earlier_steps(self):
        """resume_from 이전 스텝은 SKIPPED 로 기록되고 실행되지 않는다."""
        recipe = RecipeDefinition(
            name="resume-test",
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="setup",
                    content="exit 1",  # 실행되면 실패할 것 — 그러나 SKIPPED 처리
                ),
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="resume-here",
                    content="resumed",
                ),
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="after",
                    content="after",
                ),
            ],
        )
        result = await execute_recipe(recipe, resume_from="resume-here")
        assert result.success
        assert result.step_results[0].status == StepStatus.SKIPPED
        assert result.step_results[1].status == StepStatus.SUCCESS
        assert result.step_results[2].status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_resume_from_unknown_step_raises(self):
        recipe = RecipeDefinition(
            name="resume-test",
            steps=[
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="only",
                    content="x",
                ),
            ],
        )
        with pytest.raises(RecipeExecutionError, match="resume_from"):
            await execute_recipe(recipe, resume_from="missing")

    @pytest.mark.asyncio
    async def test_user_error_does_not_leak_full_stderr(self):
        """error 필드는 짧은 요약, debug_log 에는 전체 stderr 가 보존된다."""
        recipe = RecipeDefinition(
            name="error-channel",
            steps=[
                RecipeStep(
                    step_type=StepType.COMMAND,
                    name="noisy",
                    # 여러 줄 stderr 생성
                    content="printf 'line1\\nline2-secret-path\\nline3' 1>&2; exit 9",
                ),
            ],
        )
        result = await execute_recipe(recipe)
        assert not result.success
        step = result.step_results[0]
        # 사용자 노출 요약은 한 줄만 포함해야 한다.
        assert "line1" in step.error
        assert "line2-secret-path" not in step.error
        # debug_log 에는 전체 내용이 보존된다.
        assert "line2-secret-path" in step.debug_log
        assert "exit=9" in step.debug_log

    @pytest.mark.asyncio
    async def test_step_result_success_property_compat(self):
        """기존 ``s.success`` 호출자는 status 기반에서도 동일하게 동작해야 한다."""
        recipe = RecipeDefinition(
            name="compat",
            steps=[
                RecipeStep(
                    step_type=StepType.PROMPT,
                    name="ok",
                    content="hi",
                ),
            ],
        )
        result = await execute_recipe(recipe)
        assert result.step_results[0].success is True
        assert result.step_results[0].failed is False
        assert result.step_results[0].skipped is False

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
