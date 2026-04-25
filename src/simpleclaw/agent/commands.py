"""슬래시 명령어 핸들러 모듈 — /cron, /recipe 처리.

사용자가 입력한 슬래시 명령어를 파싱하고 적절한 핸들러로 디스패치한다.

동작 흐름:
- /cron: CronScheduler를 통해 예약 작업의 CRUD를 수행
- /recipe-name: 레시피를 검색하고, 파라미터를 치환한 후 ReAct 루프로 실행
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from simpleclaw.recipes.loader import discover_recipes

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler

logger = logging.getLogger(__name__)


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

async def try_recipe_command(text: str, react_loop_fn) -> str | None:
    """텍스트가 ``/recipe-name`` 명령인지 확인하고 해당 레시피를 실행한다.

    ``react_loop_fn``은 오케스트레이터의 바인딩된 ``_react_loop`` 메서드이다.

    Returns:
        레시피 실행 결과 텍스트. 레시피 명령이 아니면 *None*.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped[1:].split(None, 1)
    if not parts:
        return None

    cmd_name = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    recipes = discover_recipes(".agent/recipes")
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
        rendered = recipe.instructions
        for p in recipe.parameters:
            if p.name not in params and p.default:
                params[p.name] = p.default

        def jinja_replacer(match):
            key = match.group(1).strip()
            return params.get(key, match.group(0))

        rendered = re.sub(r"\{\{\s*(\w+)\s*\}\}", jinja_replacer, rendered)

        def shell_replacer(match):
            key = match.group(1)
            return params.get(key, match.group(0))

        rendered = re.sub(r"\$\{(\w+)\}", shell_replacer, rendered)

        return await react_loop_fn(rendered)

    return f"레시피 '{cmd_name}'에 instructions가 정의되어 있지 않습니다."
