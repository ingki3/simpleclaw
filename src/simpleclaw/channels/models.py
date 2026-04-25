"""채널 통신 데이터 모델.

외부 채널(Telegram, Webhook)에서 사용하는 공통 데이터 구조를 정의한다.
- 채널별 예외 계층 (ChannelError → TelegramError / WebhookError)
- 접근 제어 로그 기록용 AccessAttempt
- 웹훅 이벤트 페이로드 WebhookEvent 및 액션 타입 열거
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ChannelError(Exception):
    """채널 작업의 기본 예외 클래스."""


class TelegramError(ChannelError):
    """텔레그램 봇 작업 중 발생하는 예외."""


class WebhookError(ChannelError):
    """웹훅 작업 중 발생하는 예외."""


class EventActionType(Enum):
    """웹훅 이벤트가 트리거하는 액션 유형.

    PROMPT: 자유 텍스트 프롬프트 실행
    RECIPE: 미리 정의된 레시피 실행
    """

    PROMPT = "prompt"
    RECIPE = "recipe"


@dataclass
class AccessAttempt:
    """접근 제어 로그 항목.

    채널(telegram/webhook)별로 인증 시도를 기록하여 보안 감사에 활용한다.
    """

    source: str  # "telegram" 또는 "webhook"
    user_identifier: str
    timestamp: datetime = field(default_factory=datetime.now)
    authorized: bool = False
    details: str = ""


@dataclass
class WebhookEvent:
    """수신된 웹훅 이벤트 페이로드.

    외부 시스템에서 POST로 전송한 이벤트 데이터를 구조화한다.
    action_type이 지정되면 해당 액션(프롬프트/레시피)을 자동 실행한다.
    """

    event_type: str
    action_type: EventActionType | None = None
    action_reference: str = ""
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
