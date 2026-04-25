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
