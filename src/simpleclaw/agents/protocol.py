"""서브에이전트 표준 응답 프로토콜.

서브프로세스로 실행되는 서브에이전트가 stdout으로 반환해야 하는 JSON 응답의
표준 스키마를 정의하고, 응답을 파싱·검증하는 도구를 제공한다.

설계 결정:
- pydantic v2 기반 검증으로 타입·필수 필드·status 분기 규칙을 한 번에 강제한다.
- `status="success"`이면 `data` 필드가 의미 있고 `error`는 None이어야 한다.
- `status="error"`이면 `error` 필드가 필수이며 `data`는 의미 없는 값으로 간주된다.
- 비표준/잘못된 응답은 예외 대신 `ValidationFailure`로 정규화하여
  spawner가 사용자에게 안전한 에러 결과로 변환할 수 있도록 한다.
- 스키마는 보수적으로 닫혀 있되(`extra="ignore"`) `meta`는 자유 형식 dict이다 —
  서브에이전트 측에서 trace_id, agent_id, version 등을 자유롭게 첨부할 수 있다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubAgentErrorDetail(BaseModel):
    """에러 응답의 상세 정보. 서브에이전트가 구조화된 에러를 보낼 때 사용한다."""

    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    message: str
    details: dict[str, Any] | None = None


class SubAgentResponse(BaseModel):
    """서브에이전트 stdout JSON의 표준 스키마.

    필수: status. 선택: data, error, meta.
    success일 때는 data 필드가 의미를 가지며, error일 때는 error 필드가 반드시 채워져야 한다.
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["success", "error"]
    data: dict[str, Any] | None = Field(default=None)
    error: str | SubAgentErrorDetail | None = Field(default=None)
    meta: dict[str, Any] | None = Field(default=None)

    @model_validator(mode="after")
    def _check_status_consistency(self) -> "SubAgentResponse":
        """status와 동반 필드의 정합성을 강제한다.

        - error 상태인데 error 필드가 비어 있으면 검증 실패 — 디버깅 정보가 누락된다.
        - success 상태에서 error가 채워져 있으면 의미가 모호하므로 검증 실패.
        """
        if self.status == "error" and self.error is None:
            raise ValueError("status='error' requires non-empty 'error' field")
        if self.status == "success" and self.error is not None:
            raise ValueError("status='success' must not include 'error' field")
        return self

    def error_text(self) -> str | None:
        """error 필드를 사람이 읽을 수 있는 단일 문자열로 정규화한다."""
        if self.error is None:
            return None
        if isinstance(self.error, str):
            return self.error
        # SubAgentErrorDetail
        if self.error.code:
            return f"[{self.error.code}] {self.error.message}"
        return self.error.message


@dataclass
class ValidationFailure:
    """비표준 응답을 안전하게 분류한 결과. 예외 대신 반환되어 spawner가 에러 결과로 변환한다."""

    reason: str  # invalid_json, schema_violation, empty_output 등
    message: str  # 사람이 읽을 수 있는 요약
    raw: str  # 원본 stdout 일부(보존된 디버깅용 텍스트)


def validate_response(
    stdout_text: str,
    *,
    raw_limit: int = 500,
) -> SubAgentResponse | ValidationFailure:
    """서브에이전트 stdout을 표준 스키마로 검증한다.

    검증 실패는 예외를 던지지 않고 `ValidationFailure`로 반환하여,
    호출자(spawner)가 일관된 에러 응답으로 변환할 수 있도록 한다.

    Args:
        stdout_text: 서브에이전트가 stdout에 출력한 텍스트.
        raw_limit: 디버깅 목적으로 `ValidationFailure.raw`에 보존할 최대 문자 수.

    Returns:
        성공 시 `SubAgentResponse`, 실패 시 `ValidationFailure`.
    """
    text = stdout_text.strip()
    raw_excerpt = text[:raw_limit]

    if not text:
        return ValidationFailure(
            reason="empty_output",
            message="Sub-agent produced no stdout output",
            raw="",
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return ValidationFailure(
            reason="invalid_json",
            message=f"Invalid JSON output: {exc}",
            raw=raw_excerpt,
        )

    if not isinstance(parsed, dict):
        return ValidationFailure(
            reason="schema_violation",
            message=(
                f"Top-level JSON must be an object; got {type(parsed).__name__}"
            ),
            raw=raw_excerpt,
        )

    try:
        return SubAgentResponse.model_validate(parsed)
    except Exception as exc:
        return ValidationFailure(
            reason="schema_violation",
            message=f"Schema violation: {exc}",
            raw=raw_excerpt,
        )


__all__ = [
    "SubAgentErrorDetail",
    "SubAgentResponse",
    "ValidationFailure",
    "validate_response",
]
