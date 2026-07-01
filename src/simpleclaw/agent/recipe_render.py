"""Recipe 진단 도구가 쓰는 경량 render helper.

``recipe_validate``와 ``recipe_generate``는 loader/import 경계에서도 안전하게 import돼야 한다.
따라서 이 모듈은 ``simpleclaw.recipes.executor``를 import하지 않고, recipe instructions와
legacy step content preview에 필요한 최소 치환 로직만 제공한다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def render_instructions_preview(
    instructions: str,
    variables: dict[str, str] | None = None,
) -> str:
    """instructions 텍스트에 내장 KST 변수와 사용자 변수를 치환한다.

    Runtime executor의 ``render_instructions``와 같은 placeholder 규칙을 사용하지만,
    operator 진단 도구의 import 순환을 피하기 위해 독립 구현으로 유지한다.
    """
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    all_vars = {
        "today": now.strftime("%Y-%m-%d"),
        "today_ko": now.strftime("%Y년 %m월 %d일"),
        "weekday": now.strftime("%A"),
        "now": now.strftime("%Y-%m-%d %H:%M"),
    }
    if variables:
        all_vars.update(variables)

    result = instructions
    for key, value in all_vars.items():
        result = result.replace("{{ " + key + " }}", value)
        result = result.replace("{{" + key + "}}", value)
    return result


def substitute_step_variables(content: str, variables: dict[str, str]) -> str:
    """legacy step content의 ``${name}`` 변수를 치환한다."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    return re.sub(r"\$\{(\w+)\}", replacer, content)


__all__ = ["render_instructions_preview", "substitute_step_variables"]
