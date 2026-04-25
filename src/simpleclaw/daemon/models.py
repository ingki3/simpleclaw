"""데몬 서브시스템의 데이터 모델 정의.

크론 작업, 실행 기록, 하트비트 틱, 대기 상태, 데몬 상태 등
데몬 전체에서 사용하는 데이터 클래스와 예외 클래스를 정의한다.
모든 모델은 불변 데이터 전달 객체(DTO)로 설계되었으며,
SQLite 영속화는 store.py에서 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ActionType(Enum):
    """크론 작업이 실행하는 액션의 유형."""

    PROMPT = "prompt"    # LLM 프롬프트 실행
    RECIPE = "recipe"    # 레시피(다단계 스크립트) 실행


class ExecutionStatus(Enum):
    """크론 작업 실행의 상태."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class DaemonError(Exception):
    """데몬 관련 연산의 기본 예외 클래스."""


class DaemonLockError(DaemonError):
    """PID 잠금을 획득할 수 없을 때 발생한다."""


class CronJobNotFoundError(DaemonError):
    """요청한 크론 작업이 존재하지 않을 때 발생한다."""


class WaitStateNotFoundError(DaemonError):
    """요청한 대기 상태가 존재하지 않을 때 발생한다."""


@dataclass
class CronJob:
    """사용자가 정의한 예약 작업.

    name: 작업의 고유 식별자
    cron_expression: 5필드 크론 표현식 (분 시 일 월 요일)
    action_type: 실행할 액션 유형 (PROMPT 또는 RECIPE)
    action_reference: 프롬프트 텍스트 또는 레시피 파일 경로
    """

    name: str
    cron_expression: str
    action_type: ActionType
    action_reference: str
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class CronJobExecution:
    """크론 작업의 단일 실행 기록."""

    job_name: str
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    result_summary: str = ""
    error_details: str = ""
    id: int | None = None


@dataclass
class HeartbeatTick:
    """특정 틱 시점의 데몬 상태 스냅샷."""

    timestamp: datetime = field(default_factory=datetime.now)
    dirty_state: bool = False
    pending_task_count: int = 0
    flush_performed: bool = False
    cron_jobs_active: int = 0


@dataclass
class WaitState:
    """일시 정지된 태스크의 직렬화된 상태.

    task_id: 태스크 고유 식별자
    serialized_state: JSON으로 직렬화된 태스크 컨텍스트
    condition_type: 대기 조건의 유형 (예: 'api_response', 'user_confirm')
    timeout_seconds: 타임아웃까지의 대기 시간 (초)
    """

    task_id: str
    serialized_state: str
    condition_type: str
    registered_at: datetime = field(default_factory=datetime.now)
    timeout_seconds: int = 3600
    resolved_at: datetime | None = None
    resolution: str | None = None


@dataclass
class DaemonState:
    """데몬의 키-값 싱글턴 상태 항목."""

    key: str
    value: str
