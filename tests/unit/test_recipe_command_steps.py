"""BIZ-421 — 수동 슬래시 명령의 v1(steps 기반) 레시피 실행 검증.

배경: `/agent-study-daily` 같은 command-step bridge 레시피는 의도적으로
``instructions:`` 없이 ``steps:`` 만 가진다(v1). cron scheduler 는 이를
``execute_recipe()`` 로 실행하지만, 수동 슬래시 경로(`try_recipe_command`)는
기존에 'instructions가 정의되어 있지 않습니다' 오류를 반환했다.

검증 포인트:
1. steps 기반 레시피가 슬래시 명령으로 실행되고 stdout 이 응답에 반영된다.
2. instructions 누락 오류가 더 이상 반환되지 않는다.
3. command 실패 시 error summary 만 노출되고 debug log(stderr 전체)는 감춘다.
4. 파라미터(key=value)가 스텝 변수로 치환된다.
5. command guard 를 react_loop_fn 의 bound object 에서 가져와 적용한다.
6. recipe start/complete/fail progress 이벤트가 발행된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.agent.commands import try_recipe_command


def _write_steps_recipe(recipes_dir: Path, name: str, body: str) -> Path:
    """v1(steps) 레시피를 ``recipes_dir/<name>/recipe.yaml`` 에 쓴다."""
    rdir = recipes_dir / name
    rdir.mkdir(parents=True, exist_ok=True)
    rfile = rdir / "recipe.yaml"
    rfile.write_text(body, encoding="utf-8")
    return rfile


async def _unused_react_loop(rendered, **kwargs):
    raise AssertionError("v1 steps 경로는 react_loop_fn 을 호출하지 않아야 한다")


class TestStepsRecipeSlashCommand:
    """`/<name>` 슬래시 명령의 v1 steps 레시피 실행."""

    @pytest.mark.asyncio
    async def test_command_step_runs_and_returns_stdout(self, tmp_path):
        recipes_dir = tmp_path / "recipes"
        _write_steps_recipe(
            recipes_dir,
            "study-daily",
            "name: study-daily\n"
            "description: command bridge\n"
            "steps:\n"
            "  - type: command\n"
            "    name: run\n"
            "    content: echo daily-study-done\n",
        )

        result = await try_recipe_command(
            "/study-daily",
            _unused_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        assert result is not None
        text, name = result
        assert name == "study-daily"
        assert "daily-study-done" in text
        # 회귀: instructions 누락 오류가 더 이상 반환되지 않는다.
        assert "instructions가 정의되어 있지 않습니다" not in text

    @pytest.mark.asyncio
    async def test_command_only_recipe_without_stdout_reports_completion(
        self, tmp_path,
    ):
        """부수효과만 있는 COMMAND 스텝(출력 없음)은 완료 요약으로 폴백한다."""
        recipes_dir = tmp_path / "recipes"
        _write_steps_recipe(
            recipes_dir,
            "silent",
            "name: silent\n"
            "description: side-effect only\n"
            "steps:\n"
            "  - type: command\n"
            "    name: touch\n"
            f"    content: touch {tmp_path / 'marker'}\n",
        )

        result = await try_recipe_command(
            "/silent",
            _unused_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        assert result is not None
        assert result[0] == "Recipe completed: 1/1 steps succeeded"
        assert (tmp_path / "marker").exists()

    @pytest.mark.asyncio
    async def test_failed_command_returns_summary_without_debug_log(
        self, tmp_path,
    ):
        """실패 시 error summary 는 포함하되 stderr 전체 debug log 는 감춘다."""
        recipes_dir = tmp_path / "recipes"
        _write_steps_recipe(
            recipes_dir,
            "broken",
            "name: broken\n"
            "description: failing bridge\n"
            "steps:\n"
            "  - type: command\n"
            "    name: fail\n"
            "    content: \"echo SECRET-DEBUG-DETAIL >&2; exit 3\"\n",
        )

        result = await try_recipe_command(
            "/broken",
            _unused_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        assert result is not None
        text, _ = result
        assert "Recipe failed: 0/1 steps succeeded" in text
        # 사용자 노출용 한 줄 요약(exit code 포함)은 있어야 한다.
        assert "Exit code 3" in text
        # debug_log 포맷(stderr/stdout 전체 덤프)은 사용자에게 노출되지 않는다.
        assert "--- stderr ---" not in text
        assert "--- stdout ---" not in text

    @pytest.mark.asyncio
    async def test_params_substituted_into_step_variables(self, tmp_path):
        """key=value 파라미터가 ${var} 스텝 변수로 치환된다."""
        recipes_dir = tmp_path / "recipes"
        _write_steps_recipe(
            recipes_dir,
            "greet",
            "name: greet\n"
            "description: parameterized\n"
            "parameters:\n"
            "  - name: who\n"
            "    default: world\n"
            "steps:\n"
            "  - type: command\n"
            "    name: say\n"
            "    content: echo hello-${who}\n",
        )

        result = await try_recipe_command(
            "/greet who=simpleclaw",
            _unused_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )
        assert result is not None
        assert "hello-simpleclaw" in result[0]

        # 파라미터 생략 시 기본값 적용 (execute_recipe 내부 규약).
        result = await try_recipe_command(
            "/greet",
            _unused_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )
        assert result is not None
        assert "hello-world" in result[0]

    @pytest.mark.asyncio
    async def test_command_guard_taken_from_bound_react_loop(self, tmp_path):
        """react_loop_fn 의 bound object(_command_guard)로 위험 명령을 차단한다."""
        from simpleclaw.security import CommandGuard

        recipes_dir = tmp_path / "recipes"
        _write_steps_recipe(
            recipes_dir,
            "danger",
            "name: danger\n"
            "description: guarded\n"
            "steps:\n"
            "  - type: command\n"
            "    name: wipe\n"
            "    content: rm -rf /\n",
        )

        class FakeOrchestrator:
            """orchestrator 처럼 _command_guard 를 가진 bound 객체."""

            def __init__(self):
                self._command_guard = CommandGuard(enabled=True)

            async def _tool_loop(self, rendered, **kwargs):
                raise AssertionError("v1 경로는 tool loop 를 호출하지 않는다")

        agent = FakeOrchestrator()
        result = await try_recipe_command(
            "/danger",
            agent._tool_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        assert result is not None
        text, _ = result
        assert "Recipe failed" in text
        assert "Command blocked" in text

    @pytest.mark.asyncio
    async def test_progress_events_emitted_for_steps_recipe(self, tmp_path):
        """v1 경로도 recipe start/complete 이벤트를 step count detail 로 발행한다."""
        recipes_dir = tmp_path / "recipes"
        _write_steps_recipe(
            recipes_dir,
            "evented",
            "name: evented\n"
            "description: progress\n"
            "steps:\n"
            "  - type: command\n"
            "    name: run\n"
            "    content: echo ok\n",
        )

        events = []

        async def on_progress(event):
            events.append(event)

        result = await try_recipe_command(
            "/evented",
            _unused_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
            on_progress=on_progress,
        )

        assert result is not None
        recipe_level = [
            (e.name, e.status, e.detail) for e in events
            if e.kind == "recipe" and e.name == "evented"
        ]
        assert recipe_level == [
            ("evented", "start", "1 steps"),
            ("evented", "complete", "1 steps"),
        ]
