"""슬래시 명령어 핸들러 모듈 — /cron, /recipe 처리.

사용자가 입력한 슬래시 명령어를 파싱하고 적절한 핸들러로 디스패치한다.

동작 흐름:
- /cron: CronScheduler를 통해 예약 작업의 CRUD를 수행
- /recipe-name: 레시피를 검색하고, 파라미터를 치환한 후 실행
  - v2(instructions): 변수 렌더링 후 ReAct 루프(LLM)로 실행
  - v1(steps): cron scheduler 와 동일하게 execute_recipe 로 스텝 직접 실행 (BIZ-421)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from simpleclaw.agent.progress import ProgressCallback, ProgressEvent, emit_progress_event
from simpleclaw.recipes.loader import discover_recipes

if TYPE_CHECKING:
    from pathlib import Path  # noqa: F401  (type annotations only)
    from simpleclaw.daemon.scheduler import CronScheduler

logger = logging.getLogger(__name__)



# ------------------------------------------------------------------
# /goal commands
# ------------------------------------------------------------------
GoalAction = Literal["help", "start", "unsupported"]


@dataclass(frozen=True)
class GoalCommand:
    """Parsed `/goal` slash command."""

    action: GoalAction
    objective: str = ""
    message: str = ""


_GOAL_USAGE = (
    "🎯 /goal 사용법:\n"
    "  /goal <목표> — 목표 달성 여부를 judge가 확인하며 최대 몇 round 반복합니다.\n"
    "예: /goal 최근 SimpleClaw 에러 로그를 확인하고 원인과 조치안을 정리해줘\n\n"
    "참고: 실행 중 /subgoal, /goal cancel, /goal status는 아직 지원하지 않습니다."
)


def parse_goal_command(text: str) -> GoalCommand | None:
    """Parse MVP `/goal` command forms. Non-goal slash commands return None."""

    stripped = text.strip()
    if not stripped.startswith("/goal"):
        return None

    parts = stripped.split(None, 1)
    if parts[0] != "/goal":
        return None

    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest or rest.lower() == "help":
        return GoalCommand(action="help", message=_GOAL_USAGE)

    if rest.lower() in {"status", "cancel", "clear", "list"}:
        return GoalCommand(
            action="unsupported",
            objective=rest,
            message=(
                f"`/goal {rest}` 는 아직 지원하지 않습니다. "
                "현재 MVP는 `/goal <목표>` foreground 실행만 지원합니다."
            ),
        )

    return GoalCommand(action="start", objective=rest)

# ------------------------------------------------------------------
# /cron commands
# ------------------------------------------------------------------

def try_cron_command(
    text: str,
    cron_scheduler: CronScheduler | None,
) -> str | None:
    """``/cron`` 명령어를 처리하여 예약 작업을 관리한다.

    Args:
        text: 사용자 입력 텍스트.
        cron_scheduler: CronScheduler 인스턴스 (없으면 에러 메시지 반환).

    Returns:
        응답 텍스트. ``/cron`` 명령이 아니면 *None*.
    """
    stripped = text.strip()
    if not stripped.startswith("/cron"):
        return None

    if cron_scheduler is None:
        return "⚠️ CronScheduler가 연결되지 않았습니다. 서버 설정을 확인해주세요."

    parts = stripped.split(None, 1)
    subcommand_text = parts[1].strip() if len(parts) > 1 else ""

    if not subcommand_text or subcommand_text == "list":
        return _cron_list(cron_scheduler)

    if subcommand_text.startswith("add "):
        return _cron_add(subcommand_text[4:].strip(), cron_scheduler)

    if subcommand_text.startswith("remove "):
        name = subcommand_text[7:].strip()
        return _cron_remove(name, cron_scheduler)

    if subcommand_text.startswith("enable "):
        name = subcommand_text[7:].strip()
        return _cron_enable(name, cron_scheduler)

    if subcommand_text.startswith("disable "):
        name = subcommand_text[8:].strip()
        return _cron_disable(name, cron_scheduler)

    return (
        "📋 /cron 사용법:\n"
        "  /cron list — 등록된 작업 목록\n"
        "  /cron add <이름> <cron식> recipe <레시피경로>\n"
        "  /cron add <이름> <cron식> prompt <프롬프트>\n"
        "  /cron remove <이름>\n"
        "  /cron enable <이름>\n"
        "  /cron disable <이름>"
    )


def _cron_list(cron_scheduler: CronScheduler) -> str:
    """등록된 cron 작업 목록을 반환한다 (builtin_tools의 공유 함수에 위임)."""
    from simpleclaw.agent.builtin_tools import _cron_list as bt_cron_list
    return bt_cron_list(cron_scheduler)


def _cron_add(args_text: str, cron_scheduler: CronScheduler) -> str:
    """cron 작업을 새로 등록한다. 형식: <이름> <분> <시> <일> <월> <요일> recipe|prompt <대상>"""
    from simpleclaw.daemon.models import ActionType

    parts = args_text.split()
    if len(parts) < 8:
        return (
            "⚠️ 형식: /cron add <이름> <분> <시> <일> <월> <요일> "
            "recipe|prompt <레시피경로|프롬프트>"
        )

    name = parts[0]
    cron_expr = " ".join(parts[1:6])
    action_type_str = parts[6].lower()
    action_ref = " ".join(parts[7:])

    if action_type_str == "recipe":
        action_type = ActionType.RECIPE
    elif action_type_str == "prompt":
        action_type = ActionType.PROMPT
    else:
        return f"⚠️ 알 수 없는 액션 타입: '{action_type_str}'. 'recipe' 또는 'prompt'를 사용하세요."

    if cron_scheduler.get_job(name):
        return f"⚠️ 이미 '{name}' 이름의 작업이 존재합니다."

    try:
        job = cron_scheduler.add_job(
            name=name,
            cron_expression=cron_expr,
            action_type=action_type,
            action_reference=action_ref,
        )
        return (
            f"✅ Cron 작업 등록 완료!\n"
            f"  이름: **{job.name}**\n"
            f"  스케줄: `{job.cron_expression}`\n"
            f"  타입: {job.action_type.value}\n"
            f"  대상: {job.action_reference}"
        )
    except Exception as exc:
        return f"❌ 등록 실패: {str(exc)[:200]}"


def _cron_remove(name: str, cron_scheduler: CronScheduler) -> str:
    """이름으로 cron 작업을 삭제한다."""
    if cron_scheduler.remove_job(name):
        return f"🗑️ '{name}' 작업이 삭제되었습니다."
    return f"⚠️ '{name}' 작업을 찾을 수 없습니다."


def _cron_enable(name: str, cron_scheduler: CronScheduler) -> str:
    """이름으로 cron 작업을 활성화한다."""
    from simpleclaw.daemon.models import CronJobNotFoundError
    try:
        cron_scheduler.enable_job(name)
        return f"✅ '{name}' 작업이 활성화되었습니다."
    except CronJobNotFoundError:
        return f"⚠️ '{name}' 작업을 찾을 수 없습니다."


def _cron_disable(name: str, cron_scheduler: CronScheduler) -> str:
    """이름으로 cron 작업을 비활성화한다."""
    from simpleclaw.daemon.models import CronJobNotFoundError
    try:
        cron_scheduler.disable_job(name)
        return f"⏸️ '{name}' 작업이 비활성화되었습니다."
    except CronJobNotFoundError:
        return f"⚠️ '{name}' 작업을 찾을 수 없습니다."


# ------------------------------------------------------------------
# /recipe-name commands
# ------------------------------------------------------------------

async def try_recipe_command(
    text: str,
    react_loop_fn,
    recipes_dir: str | Path = "~/.simpleclaw-agent/default/recipes",
    legacy_recipes_dir: str | Path | None = ".agent/recipes",
    on_progress: ProgressCallback | None = None,
) -> tuple[str, str] | None:
    """텍스트가 ``/recipe-name`` 명령인지 확인하고 해당 레시피를 실행한다.

    ``react_loop_fn``은 오케스트레이터의 바인딩된 ``_react_loop`` 메서드이다.

    Args:
        text: 사용자 입력.
        react_loop_fn: 오케스트레이터 ``_tool_loop``.
        recipes_dir: 1차 레시피 디렉터리 — BIZ-202 이후 절대 경로 권장
            (기본 ``~/.simpleclaw-agent/default/recipes``). 봇/데몬이 동일 경로를 보도록 한다.
        legacy_recipes_dir: BIZ-202 이전 위치 폴백. 기본 ``.agent/recipes``
            (프로젝트 working tree). ``None`` 이면 폴백 비활성.
        on_progress: BIZ-329 — recipe 명령 시작/완료/실패 이벤트 콜백.

    Returns:
        ``(result_text, recipe_name)`` 튜플. 레시피 명령이 아니면 *None*.
        ``recipe_name`` 은 BIZ-76 에서 추가됨 — 호출자(orchestrator) 가
        ``ConversationMessage.channel`` 에 ``"recipe:<name>"`` 형식으로
        붙여 dreaming 자동 트리거 필터가 잡을 수 있게 한다.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped[1:].split(None, 1)
    if not parts:
        return None

    cmd_name = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    recipes = discover_recipes(recipes_dir, legacy_dir=legacy_recipes_dir)
    recipes_by_name = {r.name: r for r in recipes}

    recipe = recipes_by_name.get(cmd_name)
    if recipe is None:
        return None

    logger.info("Recipe command: /%s", cmd_name)

    # key=value 형식의 파라미터 파싱
    params = {}
    if rest:
        for match in re.finditer(r'(\w+)=(?:"([^"]*)"|(\S+))', rest):
            key = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            params[key] = value

    if recipe.instructions:
        # 파라미터 기본값 적용
        for p in recipe.parameters:
            if p.name not in params and p.default:
                params[p.name] = p.default

        # 내장 변수(today 등) + 사용자 변수를 한 번에 치환
        from simpleclaw.recipes.executor import render_instructions
        rendered = render_instructions(recipe.instructions, variables=params)

        await emit_progress_event(
            on_progress, ProgressEvent("recipe", cmd_name, "start", "instructions")
        )
        try:
            result = await react_loop_fn(rendered, on_progress=on_progress)
        except TypeError:
            # 기존 테스트/호출자가 1-arg react_loop_fn 을 쓰는 경우 하위 호환.
            result = await react_loop_fn(rendered)
        except Exception as exc:
            await emit_progress_event(
                on_progress, ProgressEvent("recipe", cmd_name, "fail", str(exc))
            )
            raise
        await emit_progress_event(
            on_progress, ProgressEvent("recipe", cmd_name, "complete", "instructions")
        )
        return result, cmd_name

    # BIZ-421 — v1(steps 기반) 레시피: cron scheduler(_execute_action) 와 동일하게
    # execute_recipe 로 command/prompt 스텝을 직접 실행한다. instructions 를
    # 추가하면 scheduler 가 v2 로 간주해 command 스텝을 건너뛰므로, 수동 슬래시
    # 경로가 v1 을 지원하는 것이 올바른 봉합 지점이다.
    if recipe.steps:
        from simpleclaw.recipes.executor import execute_recipe
        from simpleclaw.recipes.models import RecipeExecutionError

        # command guard 는 orchestrator 바인딩 메서드(`react_loop_fn.__self__`)에서
        # 가져온다 — scheduler 의 `getattr(self._agent, "_command_guard", None)` 과
        # 같은 규약. 테스트 등 unbound 함수가 넘어오면 guard 없이 실행된다.
        guard = getattr(
            getattr(react_loop_fn, "__self__", None), "_command_guard", None
        )

        try:
            # recipe/step start·complete·fail 이벤트는 execute_recipe 가
            # step count detail 과 함께 직접 발행한다.
            result = await execute_recipe(
                recipe,
                variables=params,
                command_guard=guard,
                on_progress=on_progress,
            )
        except RecipeExecutionError as exc:
            # 필수 파라미터 누락 등 실행 전 검증 실패 — traceback 없이 요약만 노출.
            await emit_progress_event(
                on_progress, ProgressEvent("recipe", cmd_name, "fail", str(exc))
            )
            return f"레시피 '{cmd_name}' 실행 실패: {exc}", cmd_name

        # 상세 stdout/stderr 는 사용자 채널이 아닌 운영 로그로만 남긴다.
        if result.debug_log:
            logger.debug("Recipe '%s' debug log:\n%s", cmd_name, result.debug_log)

        succeeded = sum(1 for s in result.step_results if s.success)
        total = len(result.step_results)

        if result.success:
            output = "\n".join(
                s.output for s in result.step_results if s.output and s.success
            )
            return (
                output or f"Recipe completed: {succeeded}/{total} steps succeeded",
                cmd_name,
            )

        summary = f"Recipe failed: {succeeded}/{total} steps succeeded"
        if result.error_summary:
            # 사용자 노출용 한 줄 요약만 — debug_log(stderr 전체)는 노출하지 않는다.
            summary += f"\n{result.error_summary}"
        return summary, cmd_name

    return (
        f"레시피 '{cmd_name}'에 instructions가 정의되어 있지 않습니다.",
        cmd_name,
    )
