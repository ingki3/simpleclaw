"""런타임 progress 이벤트 모델과 사용자 노출용 compact 포맷터.

Telegram streaming 중 tool/skill/command/recipe 실행 상태를 LLM 생성 텍스트가
아닌 실제 런타임 이벤트 기반으로 표시하기 위한 작은 공용 모듈이다. 출력은 사용자
가시 영역에 들어가므로 secret-like 값은 여기에서 한 번 더 방어적으로 마스킹한다.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

ProgressKind = Literal["tool", "skill", "command", "recipe", "goal", "complex_fact"]
ProgressStatus = Literal["start", "complete", "fail"]
ProgressCallback = Callable[["ProgressEvent"], Awaitable[None] | None]

_SECRET_KEY_RE = re.compile(
    r"(?i)\b(token|api[_-]?key|password|passwd|secret|authorization)\b\s*[:=]\s*"
    r"(['\"]?)([^\s,'\"}]+)(\2)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,'\"]+")
_LONG_SECRETISH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-]{24,}(?![A-Za-z0-9])")
_STATUS_LABELS = {"start": "시작", "complete": "완료", "fail": "실패"}
_KIND_ICONS = {
    "tool": "🛠️",
    "skill": "🧩",
    "command": "💻",
    "recipe": "📋",
    "goal": "🎯",
    "complex_fact": "🔎",
}
# BIZ-425 — complex fact workflow 의 내부 슬롯 이름을 사용자 친화 한국어로
# 표시하기 위한 라벨 맵. 내부 영어 slot question 은 사용자에게 노출하지 않는다
# (상세 slot/question 은 structured log 에만 남긴다).
_COMPLEX_FACT_LABELS = {
    "current_state": "최신 상태 확인",
    "comparison_set": "비교 대상 확인",
    "calculation_inputs": "계산 입력 확인",
    "decision_rules": "판정 기준 확인",
    "remaining_variables": "남은 변수 확인",
    "conflict_resolution": "상충 정보 정리",
    "impact_analysis": "영향 분석 확인",
}
# complex fact detail dict 에서 사용자 노출을 막을 내부 필드.
_COMPLEX_FACT_HIDDEN_DETAIL_KEYS = frozenset({"question"})


@dataclass(frozen=True)
class ProgressEvent:
    """도구/스킬/명령/레시피 실행 중 사용자에게 노출할 상태 이벤트."""

    kind: ProgressKind
    name: str
    status: ProgressStatus
    detail: Any | None = None


def redact_secrets(value: Any, *, limit: int = 160) -> str:
    """사용자 노출 전 secret-like 값을 ``[REDACTED]`` 로 치환하고 한 줄로 압축한다."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            text = str(value)
    text = " ".join(text.split())
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}={m.group(2)}[REDACTED]{m.group(4)}", text)
    text = _LONG_SECRETISH_RE.sub("[REDACTED]", text)
    if len(text) > limit:
        return text[: max(limit - 1, 0)] + "…"
    return text


def format_progress_line(event: ProgressEvent) -> str:
    """단일 progress 이벤트를 Telegram placeholder 에 들어갈 compact 한 줄로 만든다.

    BIZ-425: ``complex_fact`` 이벤트는 내부 슬롯 이름(current_state 등)과 영어
    slot question 을 그대로 노출하지 않고 한국어 라벨로 바꿔 표시한다.
    """
    icon = _KIND_ICONS.get(event.kind, "•")
    label = _STATUS_LABELS.get(event.status, event.status)
    name = event.name or event.kind
    detail_value = event.detail
    if event.kind == "complex_fact":
        name = _COMPLEX_FACT_LABELS.get(event.name, name)
        if isinstance(detail_value, dict):
            detail_value = {
                key: value
                for key, value in detail_value.items()
                if key not in _COMPLEX_FACT_HIDDEN_DETAIL_KEYS
            } or None
    head = f"{icon} {name} {label}"
    detail = redact_secrets(detail_value)
    return f"{head} — {detail}" if detail else head


async def emit_progress_event(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    """콜백이 있으면 progress 이벤트를 발행하되 UI 실패가 런타임을 막지 않게 한다."""
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result
