"""Trace ID 전파용 컨텍스트 모듈.

분산 트레이싱을 위한 trace_id를 ``contextvars.ContextVar``로 관리한다.
- 메시지 진입점에서 ``new_trace_id()`` 또는 ``set_trace_id()``로 발급/주입
- 같은 비동기 호출 체인 내의 모든 코드가 ``get_trace_id()``로 동일 값을 조회
- 서브프로세스 경계는 contextvars로 넘어가지 않으므로, 환경변수
  (``SIMPLECLAW_TRACE_ID``)로 명시적으로 전달해야 한다 — 이 모듈의
  ``TRACE_ID_ENV_VAR``와 ``inject_trace_id_env()`` 헬퍼를 활용한다.

설계 결정:
- ``ContextVar``는 ``copy_context()``로 자식 태스크에 자동 복사되므로
  ``asyncio.create_task``로 띄운 백그라운드 임베딩 태스크에도 trace_id가 자연스럽게 전파된다.
- ``set_trace_id``는 토큰을 반환하여 ``with``-style 복원을 가능하게 한다.
- trace_id가 비어 있을 때 로그 필드는 빈 문자열로 남기고 강제 발급은 하지 않는다 —
  데몬·테스트 등 정상 진입점이 아닌 컨텍스트에서의 잡음을 줄이기 위함.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from contextlib import contextmanager
from typing import Iterator

# 환경변수 키 — 서브프로세스(스킬/서브에이전트)에 trace_id를 전달할 때 사용.
TRACE_ID_ENV_VAR = "SIMPLECLAW_TRACE_ID"

# 빈 문자열이 기본값 — "발급되지 않음"을 명시적으로 표현한다.
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "simpleclaw_trace_id", default=""
)


def new_trace_id() -> str:
    """새로운 trace_id(uuid4 hex)를 생성하여 현재 컨텍스트에 설정하고 반환한다."""
    trace_id = uuid.uuid4().hex
    _trace_id_var.set(trace_id)
    return trace_id


def set_trace_id(trace_id: str) -> contextvars.Token[str]:
    """주어진 trace_id를 현재 컨텍스트에 설정하고 복원용 토큰을 반환한다.

    크론 잡·웹훅 등 외부에서 들어온 trace_id를 명시적으로 주입할 때 사용한다.
    """
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: contextvars.Token[str]) -> None:
    """``set_trace_id``가 반환한 토큰으로 이전 값을 복원한다."""
    _trace_id_var.reset(token)


def get_trace_id() -> str:
    """현재 컨텍스트의 trace_id를 반환한다. 미설정 시 빈 문자열."""
    return _trace_id_var.get()


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    """``with`` 블록 동안만 trace_id를 활성화한다.

    Args:
        trace_id: 사용할 trace_id. None이면 새로 발급한다.

    Yields:
        활성화된 trace_id.
    """
    effective = trace_id if trace_id else uuid.uuid4().hex
    token = _trace_id_var.set(effective)
    try:
        yield effective
    finally:
        _trace_id_var.reset(token)


def inject_trace_id_env(env: dict[str, str]) -> dict[str, str]:
    """주어진 환경변수 딕셔너리에 현재 trace_id를 주입하여 반환한다.

    현재 컨텍스트에 trace_id가 없으면 ``env``를 그대로 반환한다(서브프로세스
    환경에 빈 변수를 만들 필요는 없음). 이미 ``TRACE_ID_ENV_VAR``가 설정되어
    있다면 덮어쓰지 않는다(상위 호출자가 명시한 값 우선).
    """
    trace_id = get_trace_id()
    if not trace_id:
        return env
    if TRACE_ID_ENV_VAR in env:
        return env
    env[TRACE_ID_ENV_VAR] = trace_id
    return env


def adopt_env_trace_id() -> str:
    """프로세스 환경변수에 ``SIMPLECLAW_TRACE_ID``가 있으면 컨텍스트로 채택한다.

    서브에이전트/스킬 진입 시 부모로부터 전달된 trace_id를 ContextVar에 채워
    동일한 호출 체인의 일부로 동작하도록 한다. 환경변수가 없으면 빈 문자열.
    """
    trace_id = os.environ.get(TRACE_ID_ENV_VAR, "").strip()
    if trace_id:
        _trace_id_var.set(trace_id)
    return trace_id


__all__ = [
    "TRACE_ID_ENV_VAR",
    "adopt_env_trace_id",
    "get_trace_id",
    "inject_trace_id_env",
    "new_trace_id",
    "reset_trace_id",
    "set_trace_id",
    "trace_scope",
]
