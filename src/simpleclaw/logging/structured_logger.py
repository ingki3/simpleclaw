"""구조화된 실행 로거 — 일별 파일 로테이션.

에이전트 실행 이력을 JSONL 형식으로 일별 파일에 기록한다.
- 각 LogEntry는 액션 타입, 입출력 요약, 소요 시간, 상태, trace_id 등을 포함
- get_entries()로 특정 날짜·trace_id의 로그를 조회할 수 있음

분산 트레이싱:
``trace_id``는 메시지 진입점에서 발급되어 ``contextvars`` 기반의 호출 체인 전체로
전파된다(:mod:`simpleclaw.logging.trace_context` 참조). 로그 작성 시 호출자가
명시적으로 trace_id를 전달하지 않더라도, 현재 컨텍스트에서 자동으로 채워진다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from simpleclaw.logging.trace_context import get_trace_id

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """에이전트 액션의 구조화된 로그 항목.

    JSONL 한 줄로 직렬화되어 일별 로그 파일에 기록된다.

    trace_id는 분산 트레이싱용 식별자로, 같은 사용자 메시지에서 출발한 모든 액션
    (오케스트레이터 → 스킬 → 서브에이전트 → 백그라운드 임베딩 등)이 동일 값을
    공유한다. 진입점에서 발급되지 않은 경우 빈 문자열로 남긴다.
    """

    timestamp: str = ""
    level: str = "INFO"
    action_type: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: float = 0.0
    status: str = "success"
    trace_id: str = ""
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
        trace_id: str | None = None,
        **details: object,
    ) -> LogEntry:
        """구조화된 로그 항목을 파일에 기록한다.

        입출력 요약은 500자로 잘라내어 로그 비대화를 방지한다.
        ``trace_id``를 명시하지 않으면 현재 ``contextvars`` 컨텍스트에서 채택한다 —
        진입점에서 한 번 발급된 trace_id가 호출 체인 전체로 자동 전파된다.
        """
        # 호출자가 trace_id를 전달하지 않으면 컨텍스트에서 자동 주입(미설정 시 빈 문자열).
        effective_trace_id = trace_id if trace_id is not None else get_trace_id()
        entry = LogEntry(
            timestamp=datetime.now().isoformat(),
            level=level,
            action_type=action_type,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            duration_ms=round(duration_ms, 2),
            status=status,
            trace_id=effective_trace_id,
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

    def get_entries(
        self,
        date: str | None = None,
        limit: int = 100,
        trace_id: str | None = None,
    ) -> list[LogEntry]:
        """특정 날짜·trace_id의 로그 항목을 조회한다 (기본 날짜: 오늘).

        ``trace_id``가 주어지면 해당 ID로 태그된 항목만 반환한다 — 분산 트레이싱
        타임라인 뷰의 데이터 소스로 사용된다. 마지막 limit개 항목만 반환한다.

        과거에 trace_id 없이 기록된 로그(`trace_id=""`)는 trace_id 필터가 비어있을
        때만 포함된다.
        """
        if date is None:
            date = datetime.now().strftime("%Y%m%d")

        log_path = self._log_dir / f"execution_{date}.log"
        if not log_path.is_file():
            return []

        entries: list[LogEntry] = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # 구버전 로그는 trace_id 필드가 없으므로 빈 값을 보충해
                    # 새 dataclass 시그니처로도 안전히 역직렬화한다.
                    data.setdefault("trace_id", "")
                    try:
                        entry = LogEntry(**data)
                    except TypeError:
                        continue
                    if trace_id is not None and entry.trace_id != trace_id:
                        continue
                    entries.append(entry)
        except OSError:
            pass

        return entries[-limit:]

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def log_dir(self) -> Path:
        return self._log_dir
