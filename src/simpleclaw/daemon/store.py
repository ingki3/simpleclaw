"""데몬 서브시스템의 SQLite 영속화 계층.

크론 작업, 실행 로그, 대기 상태, 데몬 키-값 상태를 SQLite에 저장하고 조회한다.
테이블 4개(cron_jobs, cron_executions, wait_states, daemon_state)를 관리하며,
초기화 시 자동으로 스키마를 생성한다.

설계 결정:
- 단일 연결(sqlite3.connect): 데몬은 단일 프로세스이므로 연결 풀 불필요
- Row Factory 사용: 딕셔너리 스타일 접근으로 가독성 확보
- ISO 8601 문자열로 날짜 저장: SQLite의 날짜 타입 제한을 우회
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from simpleclaw.daemon.models import (
    ActionType,
    BackoffStrategy,
    CronJob,
    CronJobExecution,
    ExecutionStatus,
    WaitState,
)
from simpleclaw.db import run_daemon_migrations

logger = logging.getLogger(__name__)


class DaemonStore:
    """크론 작업, 실행 로그, 대기 상태, 데몬 상태를 SQLite로 영속화하는 저장소."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 연결을 열기 전에 마이그레이션을 적용한다 — 러너가 자체 연결을 사용하므로
        # 기존 self._conn과 락이 충돌하지 않는다. 실패 시 MigrationError가
        # 그대로 전파되어 부팅이 중단된다(일관성 우선).
        run_daemon_migrations(self._db_path)
        self._conn = sqlite3.connect(
            str(self._db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        """DB 연결을 종료한다."""
        self._conn.close()

    # --- 크론 작업 CRUD ---

    def save_job(self, job: CronJob) -> None:
        """크론 작업을 저장한다 (이미 존재하면 덮어쓰기)."""
        self._conn.execute(
            """INSERT OR REPLACE INTO cron_jobs
               (name, cron_expression, action_type, action_reference, enabled,
                created_at, updated_at,
                max_attempts, backoff_seconds, backoff_strategy,
                circuit_break_threshold, consecutive_failures)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.name,
                job.cron_expression,
                job.action_type.value,
                job.action_reference,
                int(job.enabled),
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
                int(job.max_attempts),
                float(job.backoff_seconds),
                job.backoff_strategy.value,
                int(job.circuit_break_threshold),
                int(job.consecutive_failures),
            ),
        )
        self._conn.commit()

    def get_job(self, name: str) -> CronJob | None:
        """이름으로 크론 작업을 조회한다. 없으면 None 반환."""
        row = self._conn.execute(
            "SELECT * FROM cron_jobs WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self) -> list[CronJob]:
        """모든 크론 작업을 생성 시각순으로 반환한다."""
        rows = self._conn.execute(
            "SELECT * FROM cron_jobs ORDER BY created_at"
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def delete_job(self, name: str) -> bool:
        """크론 작업을 삭제한다. 삭제 성공 여부를 반환한다."""
        cursor = self._conn.execute(
            "DELETE FROM cron_jobs WHERE name = ?", (name,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> CronJob:
        """DB 행을 CronJob 데이터 클래스로 변환한다.

        구버전 DB에서 마이그레이션된 직후의 행은 새 컬럼이 DEFAULT로 채워져 있으므로
        그대로 매핑하면 된다. 컬럼이 아직 존재하지 않을 가능성을 대비해
        ``row.keys()`` 검사로 폴백한다.
        """
        keys = set(row.keys())
        return CronJob(
            name=row["name"],
            cron_expression=row["cron_expression"],
            action_type=ActionType(row["action_type"]),
            action_reference=row["action_reference"],
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            max_attempts=row["max_attempts"] if "max_attempts" in keys else 3,
            backoff_seconds=(
                row["backoff_seconds"] if "backoff_seconds" in keys else 60.0
            ),
            backoff_strategy=BackoffStrategy(
                row["backoff_strategy"]
                if "backoff_strategy" in keys
                else "exponential"
            ),
            circuit_break_threshold=(
                row["circuit_break_threshold"]
                if "circuit_break_threshold" in keys
                else 5
            ),
            consecutive_failures=(
                row["consecutive_failures"]
                if "consecutive_failures" in keys
                else 0
            ),
        )

    # --- 크론 실행 로그 ---

    def log_execution(self, execution: CronJobExecution) -> int:
        """실행 기록을 삽입하고 생성된 ID를 반환한다."""
        cursor = self._conn.execute(
            """INSERT INTO cron_executions
               (job_name, started_at, finished_at, status, result_summary,
                error_details, attempt)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                execution.job_name,
                execution.started_at.isoformat(),
                execution.finished_at.isoformat() if execution.finished_at else None,
                execution.status.value,
                execution.result_summary,
                execution.error_details,
                int(execution.attempt),
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def update_execution(self, exec_id: int, **kwargs: object) -> None:
        """실행 기록의 필드를 동적으로 업데이트한다."""
        parts = []
        values: list[object] = []
        for key, val in kwargs.items():
            if key == "status" and isinstance(val, ExecutionStatus):
                val = val.value
            elif key in ("finished_at",) and isinstance(val, datetime):
                val = val.isoformat()
            parts.append(f"{key} = ?")
            values.append(val)
        values.append(exec_id)
        self._conn.execute(
            f"UPDATE cron_executions SET {', '.join(parts)} WHERE id = ?",
            values,
        )
        self._conn.commit()

    def get_executions(
        self, job_name: str, limit: int = 10
    ) -> list[CronJobExecution]:
        """특정 작업의 최근 실행 기록을 조회한다."""
        rows = self._conn.execute(
            """SELECT * FROM cron_executions
               WHERE job_name = ? ORDER BY started_at DESC LIMIT ?""",
            (job_name, limit),
        ).fetchall()
        return [self._row_to_execution(r) for r in rows]

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> CronJobExecution:
        """DB 행을 CronJobExecution 데이터 클래스로 변환한다."""
        keys = set(row.keys())
        return CronJobExecution(
            id=row["id"],
            job_name=row["job_name"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None
            ),
            status=ExecutionStatus(row["status"]),
            result_summary=row["result_summary"] or "",
            error_details=row["error_details"] or "",
            attempt=row["attempt"] if "attempt" in keys else 1,
        )

    # --- 대기 상태 ---

    def save_wait_state(self, state: WaitState) -> None:
        """대기 상태를 저장한다 (이미 존재하면 덮어쓰기)."""
        self._conn.execute(
            """INSERT OR REPLACE INTO wait_states
               (task_id, serialized_state, condition_type, registered_at, timeout_seconds, resolved_at, resolution)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                state.task_id,
                state.serialized_state,
                state.condition_type,
                state.registered_at.isoformat(),
                state.timeout_seconds,
                state.resolved_at.isoformat() if state.resolved_at else None,
                state.resolution,
            ),
        )
        self._conn.commit()

    def get_wait_state(self, task_id: str) -> WaitState | None:
        """task_id로 대기 상태를 조회한다. 없으면 None 반환."""
        row = self._conn.execute(
            "SELECT * FROM wait_states WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_wait_state(row)

    def get_pending_waits(self) -> list[WaitState]:
        """미해소(pending) 상태의 대기 항목을 모두 반환한다."""
        rows = self._conn.execute(
            "SELECT * FROM wait_states WHERE resolved_at IS NULL ORDER BY registered_at"
        ).fetchall()
        return [self._row_to_wait_state(r) for r in rows]

    def resolve_wait_state(
        self, task_id: str, resolution: str
    ) -> None:
        """대기 상태를 해소 처리한다 (resolved_at 및 resolution 기록)."""
        self._conn.execute(
            "UPDATE wait_states SET resolved_at = ?, resolution = ? WHERE task_id = ?",
            (datetime.now().isoformat(), resolution, task_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_wait_state(row: sqlite3.Row) -> WaitState:
        """DB 행을 WaitState 데이터 클래스로 변환한다."""
        return WaitState(
            task_id=row["task_id"],
            serialized_state=row["serialized_state"],
            condition_type=row["condition_type"],
            registered_at=datetime.fromisoformat(row["registered_at"]),
            timeout_seconds=row["timeout_seconds"],
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None
            ),
            resolution=row["resolution"],
        )

    # --- 데몬 상태 (키-값 저장) ---

    def get_state(self, key: str) -> str | None:
        """키에 해당하는 데몬 상태 값을 조회한다. 없으면 None 반환."""
        row = self._conn.execute(
            "SELECT value FROM daemon_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        """데몬 상태 값을 저장한다 (이미 존재하면 덮어쓰기)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO daemon_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()
