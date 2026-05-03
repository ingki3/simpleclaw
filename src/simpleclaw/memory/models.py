"""메모리 시스템의 데이터 모델을 정의하는 모듈.

대화 메시지(ConversationMessage), 핵심 기억(MemoryEntry), 역할 열거형(MessageRole) 등
메모리 패키지 전체에서 공통으로 사용하는 자료 구조와 예외 클래스를 포함한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import numpy as np


class MessageRole(Enum):
    """대화 메시지의 역할(발화자)을 나타내는 열거형."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ConversationMessage:
    """대화 이력의 단일 메시지를 표현하는 데이터 클래스.

    Attributes:
        role: 메시지 발화자 역할 (user/assistant/system).
        content: 메시지 텍스트 내용.
        timestamp: 메시지 생성 시각. 기본값은 현재 시각.
        token_count: 메시지의 토큰 수. 프롬프트 버짓 관리에 사용된다.
        channel: 메시지가 들어온 채널 식별자(예: "telegram", "webhook",
            "console", "cron"). BIZ-77(F)에서 인사이트 source 역추적 + Admin
            UI 노출에 사용된다. ``None``이면 채널 정보가 기록되지 않은 메시지
            (마이그레이션 0002 이전 데이터 또는 producer가 미지정)이며 UI에서는
            "unknown" 으로 표시된다. 후속 BIZ-76(E) 가 cron/recipe 태깅을 채운다.
    """
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    token_count: int = 0
    channel: str | None = None


@dataclass
class MemoryEntry:
    """드리밍 프로세스에서 생성된 핵심 기억 항목.

    Attributes:
        summary: 대화에서 추출된 요약 텍스트.
        created_at: 기억 생성 시각.
        source: 기억의 출처 식별자 (예: "dreaming_2026-04-17").
    """
    summary: str
    created_at: datetime = field(default_factory=datetime.now)
    source: str = ""  # 예: "dreaming_2026-04-17"


@dataclass
class ClusterRecord:
    """시맨틱 클러스터(주제 묶음)의 영속 표현.

    Phase 3 그래프형 드리밍에서 임베딩 메시지들을 의미 단위로 그룹화한 산출물.
    centroid는 클러스터 멤버들의 임베딩 평균이며, summary는 LLM이 생성한
    클러스터 요약(MEMORY.md의 ``<!-- cluster:N -->`` 섹션 본문과 동일)이다.

    Attributes:
        id: 클러스터 행 id (저장 후 부여, 미저장 상태에서는 0).
        label: 사람이 읽을 짧은 라벨 (예: "맥북 구매 논의"). LLM이 생성.
        centroid: float32 평균 벡터(numpy). 멤버 추가 시 incremental mean으로 갱신.
        summary: LLM이 작성한 누적 요약. MEMORY.md 섹션 본문으로 사용.
        member_count: 현재 이 클러스터에 속한 메시지 수.
        updated_at: 마지막 갱신 시각 (요약 갱신 또는 멤버 추가).
    """
    id: int
    label: str
    centroid: np.ndarray
    summary: str = ""
    member_count: int = 0
    updated_at: datetime = field(default_factory=datetime.now)


class MemoryError(Exception):
    """메모리 시스템의 기본 예외 클래스."""


class DreamingError(MemoryError):
    """드리밍 프로세스 중 발생하는 예외."""
