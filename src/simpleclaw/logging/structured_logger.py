"""구조화된 실행 로거 — 일별 파일 로테이션.

에이전트 실행 이력을 JSONL 형식으로 일별 파일에 기록한다.
- 각 LogEntry는 액션 타입, 입출력 요약, 소요 시간, 상태 등을 포함
- get_entries()로 특정 날짜의 로그를 조회할 수 있음
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """에이전트 액션의 구조화된 로그 항목.

    JSONL 한 줄로 직렬화되어 일별 로그 파일에 기록된다.
    """

    timestamp: str = ""
    level: str = "INFO"
    action_type: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: float = 0.0
    status: str = "success"
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """딕셔너리로 변환한다."""
        return asdict(self)

    def to_json(self) -> str:
        """JSON 문자열로 직렬화한다 (한글 유니코드 그대로 유지)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class StructuredLogger:
    """구조화된 JSONL 로그를 일별 로테이션 파일에 기록하는 로거.

    파일명 패턴: execution_YYYYMMDD.log
    """

    def __init__(self, log_dir: str | Path = ".logs") -> None:
        self._log_dir = Path(log_dir)
        self._current_date: str = ""
        self._current_file = None
        self._entry_count = 0

    def _ensure_dir(self) -> bool:
        """로그 디렉터리를 생성한다. 실패 시 False를 반환한다."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.warning("Cannot create log directory %s: %s", self._log_dir, exc)
            return False

    def _get_log_path(self) -> Path:
        """오늘 날짜 기준 로그 파일 경로를 반환한다."""
        date_str = datetime.now().strftime("%Y%m%d")
        return self._log_dir / f"execution_{date_str}.log"

    def log(
        self,
        action_type: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: float = 0.0,
        status: str = "success",
        level: str = "INFO",
        **details: object,
    ) -> LogEntry:
        """구조화된 로그 항목을 파일에 기록한다.

        입출력 요약은 500자로 잘라내어 로그 비대화를 방지한다.
        """
        entry = LogEntry(
            timestamp=datetime.now().isoformat(),
            level=level,
            action_type=action_type,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            duration_ms=round(duration_ms, 2),
            status=status,
            details=details,
        )

        if not self._ensure_dir():
            return entry

        try:
            log_path = self._get_log_path()
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
            self._entry_count += 1
        except OSError:
            logger.warning("Failed to write log entry")

        return entry

    def get_entries(self, date: str | None = None, limit: int = 100) -> list[LogEntry]:
        """특정 날짜의 로그 항목을 조회한다 (기본: 오늘).

        마지막 limit개 항목만 반환하여 최신 이력 우선으로 제공한다.
        """
        if date is None:
            date = datetime.now().strftime("%Y%m%d")

        log_path = self._log_dir / f"execution_{date}.log"
        if not log_path.is_file():
            return []

        entries = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            entries.append(LogEntry(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue
        except OSError:
            pass

        return entries[-limit:]

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def log_dir(self) -> Path:
        return self._log_dir
