"""에이전트 모니터링용 메트릭 수집기.

스레드 안전한 카운터로 실행 횟수, 성공/실패, 토큰 사용량, 서브에이전트 생성 수 등을
추적한다. get_snapshot()으로 현재 시점의 불변 스냅샷을 얻을 수 있다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MetricsSnapshot:
    """현재 메트릭의 불변 스냅샷.

    대시보드 API 응답 등에서 스레드 안전하게 전달하기 위해 사용한다.
    """

    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_tokens_used: int = 0
    total_duration_ms: float = 0.0
    active_cron_jobs: int = 0
    sub_agent_spawns: int = 0
    error_rate: float = 0.0
    # 서브프로세스 종료/좀비 회수 메트릭 — 24/7 데몬에서 PID·메모리 누수 감지에 사용.
    process_kills_sigterm: int = 0
    process_kills_sigkill: int = 0
    process_group_leaks: int = 0
    zombies_reaped: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리로 변환한다."""
        return {
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "failed_executions": self.failed_executions,
            "total_tokens_used": self.total_tokens_used,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "active_cron_jobs": self.active_cron_jobs,
            "sub_agent_spawns": self.sub_agent_spawns,
            "error_rate": round(self.error_rate, 4),
            "process_kills_sigterm": self.process_kills_sigterm,
            "process_kills_sigkill": self.process_kills_sigkill,
            "process_group_leaks": self.process_group_leaks,
            "zombies_reaped": self.zombies_reaped,
            "timestamp": self.timestamp,
        }


class MetricsCollector:
    """스레드 안전한 에이전트 작업 메트릭 수집기.

    threading.Lock으로 동시 접근을 보호하며, 각 메트릭은 단조 증가 카운터이다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_executions = 0
        self._successful = 0
        self._failed = 0
        self._total_tokens = 0
        self._total_duration_ms = 0.0
        self._sub_agent_spawns = 0
        self._active_cron_jobs = 0
        # 서브프로세스 종료 카운터 — 누수 추세 감시에 사용.
        self._process_kills_sigterm = 0
        self._process_kills_sigkill = 0
        self._process_group_leaks = 0
        self._zombies_reaped = 0

    def record_execution(
        self,
        success: bool,
        duration_ms: float = 0.0,
        tokens_used: int = 0,
    ) -> None:
        """에이전트 실행 결과를 기록한다."""
        with self._lock:
            self._total_executions += 1
            if success:
                self._successful += 1
            else:
                self._failed += 1
            self._total_duration_ms += duration_ms
            self._total_tokens += tokens_used

    def record_sub_agent_spawn(self) -> None:
        """서브에이전트 생성을 기록한다."""
        with self._lock:
            self._sub_agent_spawns += 1

    def set_active_cron_jobs(self, count: int) -> None:
        """활성 크론 잡 수를 갱신한다."""
        with self._lock:
            self._active_cron_jobs = count

    def record_process_kill(
        self,
        *,
        killed: bool,
        group_alive: bool,
        reaped_zombies: int,
    ) -> None:
        """``kill_process_group`` 결과를 메트릭에 반영한다.

        Args:
            killed: SIGKILL이 사용되었는지 여부 (강제 종료).
            group_alive: 종료 시도 후에도 그룹이 잔존하는지 여부 (PID 누수 신호).
            reaped_zombies: 회수한 좀비 자식 프로세스 수.
        """
        with self._lock:
            if killed:
                self._process_kills_sigkill += 1
            else:
                self._process_kills_sigterm += 1
            if group_alive:
                self._process_group_leaks += 1
            if reaped_zombies > 0:
                self._zombies_reaped += reaped_zombies

    def get_snapshot(self) -> MetricsSnapshot:
        """현재 메트릭의 불변 스냅샷을 반환한다."""
        with self._lock:
            error_rate = (
                self._failed / self._total_executions
                if self._total_executions > 0
                else 0.0
            )
            return MetricsSnapshot(
                total_executions=self._total_executions,
                successful_executions=self._successful,
                failed_executions=self._failed,
                total_tokens_used=self._total_tokens,
                total_duration_ms=self._total_duration_ms,
                active_cron_jobs=self._active_cron_jobs,
                sub_agent_spawns=self._sub_agent_spawns,
                error_rate=error_rate,
                process_kills_sigterm=self._process_kills_sigterm,
                process_kills_sigkill=self._process_kills_sigkill,
                process_group_leaks=self._process_group_leaks,
                zombies_reaped=self._zombies_reaped,
                timestamp=datetime.now().isoformat(),
            )

    def reset(self) -> None:
        """모든 메트릭 카운터를 초기화한다."""
        with self._lock:
            self._total_executions = 0
            self._successful = 0
            self._failed = 0
            self._total_tokens = 0
            self._total_duration_ms = 0.0
            self._sub_agent_spawns = 0
            self._process_kills_sigterm = 0
            self._process_kills_sigkill = 0
            self._process_group_leaks = 0
            self._zombies_reaped = 0
