"""서브에이전트 스포너 데이터 모델.

서브에이전트의 생명주기(PENDING→RUNNING→SUCCESS/FAILURE/TIMEOUT/KILLED),
권한 범위(PermissionScope), 실행 결과(SubAgentResult) 등 핵심 데이터 구조를 정의한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class SubAgentStatus(Enum):
    """서브에이전트의 생명주기 상태."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    KILLED = "killed"


class SubAgentError(Exception):
    """서브에이전트 작업의 기본 예외 클래스."""


class SpawnError(SubAgentError):
    """서브에이전트 생성에 실패했을 때 발생하는 예외."""


class PoolExhaustedError(SubAgentError):
    """동시 실행 풀이 가득 차서 대기열이 없을 때 발생하는 예외."""


@dataclass
class PermissionScope:
    """서브에이전트에 적용되는 권한 제약.

    allowed_paths: 접근 허용 파일 시스템 경로 목록
    network: 네트워크 접근 허용 여부
    """

    allowed_paths: list[str] = field(default_factory=list)
    network: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed_paths": self.allowed_paths,
            "network": self.network,
        }


@dataclass
class SubAgent:
    """위임된 작업을 수행하는 서브프로세스 서브에이전트.

    스포너가 생성하고 풀에서 동시 실행 수를 관리한다.
    """

    agent_id: str
    task: str
    command: list[str]
    scope: PermissionScope
    workspace_path: Path | None = None
    status: SubAgentStatus = SubAgentStatus.PENDING
    spawn_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    exit_code: int | None = None
    timeout: int = 300


@dataclass
class SubAgentResult:
    """완료된 서브에이전트의 파싱된 출력 결과.

    서브에이전트 stdout의 JSON을 파싱하여 status, data, error를 구조화한다.
    """

    agent_id: str
    status: str
    data: dict | None = None
    error: str | None = None
    exit_code: int = 0
    execution_time: float = 0.0
