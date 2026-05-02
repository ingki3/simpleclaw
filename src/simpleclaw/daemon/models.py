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


class BackoffStrategy(Enum):
    """재시도 백오프 전략.

    LINEAR: ``backoff_seconds * attempt`` (1, 2, 3 → 60, 120, 180s)
    EXPONENTIAL: ``backoff_seconds * (2 ** (attempt - 1))`` (1, 2, 3 → 60, 120, 240s)
    """

    LINEAR = "linear"
    EXPONENTIAL = "exponential"


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

    재시도 정책 필드(BIZ-19):
    - max_attempts: 최대 실행 시도 횟수(1 = 재시도 없음). 기본 3.
    - backoff_seconds: 첫 백오프 간격(초). 기본 60.
    - backoff_strategy: ``linear`` | ``exponential``. 기본 exponential.
    - circuit_break_threshold: 누적 실패 임계값. 0 이면 비활성. 기본 5.
      한 스케줄 트리거에서 모든 재시도가 실패하면 ``consecutive_failures``가
      1 증가하고, 임계값 이상이면 작업을 자동 비활성(circuit-break)한다.
    - consecutive_failures: 연속 실패 카운터. 성공 시 0으로 리셋.

    LLM API 자체의 재시도(별도 이슈)와 책임을 분리한다.
    여기서는 작업 단위 실패만을 감지하고 다음 시도까지 백오프 후 재실행한다.
    """

    name: str
    cron_expression: str
    action_type: ActionType
    action_reference: str
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    max_attempts: int = 3
    backoff_seconds: float = 60.0
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    circuit_break_threshold: int = 5
    consecutive_failures: int = 0


@dataclass
class CronJobExecution:
    """크론 작업의 단일 실행 기록.

    한 스케줄 트리거 안에서 재시도가 발생하면 시도마다 별도 레코드를 남긴다.
    ``attempt``는 1부터 시작하며 ``max_attempts``까지 증가할 수 있다.
    """

    job_name: str
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    result_summary: str = ""
    error_details: str = ""
    id: int | None = None
    attempt: int = 1


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
