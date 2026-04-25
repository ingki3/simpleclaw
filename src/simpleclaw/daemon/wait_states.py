"""대기 상태 관리자: 일시 정지된 태스크의 직렬화 및 재개.

외부 조건(API 응답, 사용자 확인 등)을 기다리는 태스크를 직렬화하여
SQLite에 저장하고, 조건 충족 또는 타임아웃 시 해소(resolve)한다.

주요 흐름:
1. register_wait(): 태스크 상태를 JSON으로 직렬화하여 저장
2. resolve_wait(): 조건 충족 시 해소 처리
3. check_timeouts(): 하트비트 틱마다 호출되어 만료된 대기 상태를 자동 해소
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from simpleclaw.daemon.models import WaitState, WaitStateNotFoundError
from simpleclaw.daemon.store import DaemonStore

logger = logging.getLogger(__name__)


class WaitStateManager:
    """일시 정지된 태스크의 비동기 대기 상태를 관리한다."""

    def __init__(self, store: DaemonStore, default_timeout: int = 3600) -> None:
        self._store = store
        self._default_timeout = default_timeout

    def register_wait(
        self,
        task_id: str,
        state: dict,
        condition_type: str,
        timeout: int | None = None,
    ) -> WaitState:
        """일시 정지된 태스크의 새 대기 상태를 등록한다.

        Args:
            task_id: 태스크 고유 식별자
            state: 직렬화할 태스크 상태 딕셔너리
            condition_type: 대기 조건 유형
            timeout: 타임아웃 (초), 미지정 시 기본값 사용
        """
        wait = WaitState(
            task_id=task_id,
            serialized_state=json.dumps(state),
            condition_type=condition_type,
            registered_at=datetime.now(),
            timeout_seconds=timeout or self._default_timeout,
        )
        self._store.save_wait_state(wait)
        logger.info(
            "Registered wait state: task_id=%s, type=%s, timeout=%ds",
            task_id,
            condition_type,
            wait.timeout_seconds,
        )
        return wait

    def resolve_wait(
        self, task_id: str, resolution: str = "completed"
    ) -> WaitState | None:
        """조건이 충족된 대기 상태를 해소한다. 이미 해소된 경우 그대로 반환."""
        wait = self._store.get_wait_state(task_id)
        if wait is None:
            return None
        if wait.resolved_at is not None:
            return wait  # 이미 해소됨 — 중복 처리 방지

        self._store.resolve_wait_state(task_id, resolution)
        # 업데이트된 필드를 포함한 최신 상태를 다시 조회
        updated = self._store.get_wait_state(task_id)
        logger.info(
            "Resolved wait state: task_id=%s, resolution=%s",
            task_id,
            resolution,
        )
        return updated

    def get_pending(self) -> list[WaitState]:
        """미해소 상태의 대기 항목을 모두 반환한다."""
        return self._store.get_pending_waits()

    def check_timeouts(self) -> list[WaitState]:
        """만료된 대기 상태를 검사하고 자동 해소한다.

        Returns:
            타임아웃으로 해소된 대기 상태 목록
        """
        now = datetime.now()
        pending = self._store.get_pending_waits()
        timed_out = []

        for wait in pending:
            elapsed = (now - wait.registered_at).total_seconds()
            if elapsed >= wait.timeout_seconds:
                self._store.resolve_wait_state(wait.task_id, "timeout")
                timed_out.append(wait)
                logger.warning(
                    "Wait state timed out: task_id=%s (elapsed=%.0fs, limit=%ds)",
                    wait.task_id,
                    elapsed,
                    wait.timeout_seconds,
                )

        return timed_out

    def get_state_data(self, task_id: str) -> dict | None:
        """대기 상태의 직렬화된 데이터를 역직렬화하여 반환한다."""
        wait = self._store.get_wait_state(task_id)
        if wait is None:
            return None
        try:
            return json.loads(wait.serialized_state)
        except json.JSONDecodeError:
            return None
