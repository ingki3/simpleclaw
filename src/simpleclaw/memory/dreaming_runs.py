"""드리밍 사이클 운영 메트릭 저장소 (BIZ-81).

배경:
    BIZ-66 §3-K — "왜 5-03에 dreaming이 갱신되지 않았나" 같은 질문에 운영자가
    즉시 답할 수 있어야 한다. 기존에는 ``daemon_state.last_dreaming_timestamp`` 한
    값과 로그 파일만 남았기 때문에 "성공했지만 추출 결과가 비어 있어서 USER.md가
    안 변했다", "preflight 마커 누락으로 abort 됐다", "메시지가 한 줄도 없었다"
    같은 정상-스킵 케이스와 진짜 실패 케이스를 구분할 수 없었다.

이 저장소는 한 사이클당 한 줄을 JSONL 로 기록한다 — `started_at`, `ended_at`,
`input_msg_count`, `generated_insight_count`, `rejected_count`, `error?`,
`skip_reason?`. 운영자/Admin UI 가 이를 시계열로 읽어 KPI 패널과 진단 메시지를
구성한다.

설계 결정:
    - 별도 sqlite 테이블이 아닌 JSONL — InsightStore/SuggestionStore/RejectBlocklistStore
      와 같은 sidecar 패턴 (atomic rename, grep 가능, 외부 도구 친화적).
    - 단일 사이클이므로 동시 쓰기 잠금은 불필요 (드리밍은 한 번에 한 회차).
    - 무한히 누적되지 않도록 ``max_records`` 후행 잘라내기 — 운영자가 "최근 N건"
      만 알면 충분하기 때문. 기본 200건(약 6개월치, 일 1회 가정).
    - 메트릭 기록 자체는 사이클 실패의 원인이 되어선 안 된다 — append 실패는
      WARN 로그만 남기고 사이클은 그대로 성공/실패 시그널을 반환.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 표준 skip 사유 — 호출부에서 자유 문자열을 쓰지 않도록 상수화.
# 새 사유가 필요하면 여기에 먼저 추가하고 의미를 docstring 으로 박제한다.
SKIP_NO_MESSAGES = "no_messages"           # 처리할 미반영 메시지가 0건.
SKIP_PREFLIGHT_FAILED = "preflight_failed"  # Protected Section 마커 누락/오염 (어떤 파일도 수정 안 됨).
SKIP_MIDWRITE_ABORTED = "midwrite_aborted"  # 쓰기 도중 ProtectedSectionError → 백업 복원.
SKIP_EMPTY_RESULTS = "empty_results"        # LLM이 의미 있는 산출물을 만들지 못함 (메모리/인사이트/혼/에이전트 모두 빈 값).

VALID_SKIP_REASONS: tuple[str, ...] = (
    SKIP_NO_MESSAGES,
    SKIP_PREFLIGHT_FAILED,
    SKIP_MIDWRITE_ABORTED,
    SKIP_EMPTY_RESULTS,
)


@dataclass
class DreamingRunRecord:
    """드리밍 사이클 한 회차의 메트릭.

    Attributes:
        id: 회차 식별자(uuid hex). Admin UI 의 행 키.
        started_at: 사이클 시작 시각.
        ended_at: 사이클 종료 시각. 진행 중이면 None.
        input_msg_count: 분석 대상 메시지 수(자동 트리거 분리 후의 ``id_pairs`` 길이).
        generated_insight_count: 이 회차에 sidecar 에 신규/강화 적재된 InsightMeta 수.
        rejected_count: 이 회차에 reject 블록리스트로 차단되어 추출에서 누락된 수.
            (사이클 *후* 누적 거절률은 SuggestionStore 에서 별도 계산.)
        error: 예외 발생 시 메시지 한 줄 요약. 정상이면 None.
        skip_reason: 의도적으로 일찍 종료된 경우의 사유 (``VALID_SKIP_REASONS``).
            error 와 skip_reason 은 상호 배타적 — 동시에 채워지지 않는다.
        details: 자유 형식 보조 컨텍스트(예: preflight 에서 어떤 섹션이 누락이었는지).
            진단 메시지를 사람에게 보여주기 위해 사용. 운영자 UI 가 그대로 표시.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None
    input_msg_count: int = 0
    generated_insight_count: int = 0
    rejected_count: int = 0
    error: str | None = None
    skip_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float | None:
        """사이클 소요 시간(초). 진행 중이면 None."""
        if self.ended_at is None:
            return None
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    @property
    def status(self) -> str:
        """행 상태 문자열 — Admin UI 표시용. 'success' / 'skip' / 'error' / 'running'."""
        if self.ended_at is None:
            return "running"
        if self.error:
            return "error"
        if self.skip_reason:
            return "skip"
        return "success"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "input_msg_count": self.input_msg_count,
            "generated_insight_count": self.generated_insight_count,
            "rejected_count": self.rejected_count,
            "error": self.error,
            "skip_reason": self.skip_reason,
            "details": self.details or {},
        }

    @classmethod
    def from_dict(cls, d: dict) -> DreamingRunRecord:
        started_raw = d.get("started_at")
        ended_raw = d.get("ended_at")
        try:
            started_at = (
                datetime.fromisoformat(started_raw)
                if isinstance(started_raw, str)
                else datetime.now()
            )
        except ValueError:
            started_at = datetime.now()
        ended_at: datetime | None = None
        if isinstance(ended_raw, str):
            try:
                ended_at = datetime.fromisoformat(ended_raw)
            except ValueError:
                ended_at = None
        skip_reason = d.get("skip_reason")
        # 알 수 없는 사유 문자열도 그대로 보존(과거 기록과의 호환). 검증은 쓰기 시점에서만.
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            started_at=started_at,
            ended_at=ended_at,
            input_msg_count=int(d.get("input_msg_count") or 0),
            generated_insight_count=int(d.get("generated_insight_count") or 0),
            rejected_count=int(d.get("rejected_count") or 0),
            error=(str(d["error"]) if d.get("error") else None),
            skip_reason=(str(skip_reason) if skip_reason else None),
            details=dict(d.get("details") or {}),
        )


class DreamingRunStore:
    """JSONL 기반 드리밍 메트릭 저장소.

    한 회차에 보통 ``begin()`` → ``finish()`` 순서로 호출된다. ``begin()`` 은
    record 객체와 즉시 디스크에 한 줄을 추가하고(중도 크래시 시에도 "running" 행이
    남아 진단 가능), ``finish()`` 는 같은 id 의 행을 업데이트한다.

    동시 쓰기는 가정하지 않는다(드리밍은 단일 사이클).
    """

    DEFAULT_MAX_RECORDS = 200

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = DEFAULT_MAX_RECORDS,
    ) -> None:
        self._path = Path(path)
        # 너무 작은 값(0/음수)은 보존을 사실상 무력화 → 안전 기본값으로 폴백.
        self._max_records = max_records if max_records and max_records > 0 else self.DEFAULT_MAX_RECORDS

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[DreamingRunRecord]:
        """모든 행을 시간 순(오래된 → 최신) 그대로 로드. 손상된 행은 WARN 로그 후 스킵."""
        out: list[DreamingRunRecord] = []
        if not self._path.is_file():
            return out
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read dreaming runs sidecar %s: %s", self._path, exc)
            return out
        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(DreamingRunRecord.from_dict(d))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed dreaming run line %d in %s: %s",
                    line_no, self._path, exc,
                )
                continue
        return out

    def save_all(self, records: list[DreamingRunRecord]) -> None:
        """모든 행을 atomic rename 으로 다시 쓴다. ``max_records`` 초과 분은 앞에서 잘라낸다."""
        # 가장 오래된 항목부터 잘라낸다(꼬리/최신 N개 보존).
        trimmed = records[-self._max_records :] if len(records) > self._max_records else records
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for rec in trimmed:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def append(self, record: DreamingRunRecord) -> None:
        """단일 회차를 추가. 메트릭 기록 실패는 호출자(드리밍 사이클)에 전파하지 않는다."""
        try:
            existing = self.load()
            existing.append(record)
            self.save_all(existing)
        except OSError as exc:
            # 사이클 자체는 성공/실패 신호를 그대로 반환해야 하므로 로그만 남긴다.
            logger.warning("Failed to append dreaming run record: %s", exc)

    def update(self, record: DreamingRunRecord) -> None:
        """동일 ``id`` 의 행을 갱신(없으면 새로 추가). 메트릭 실패는 전파하지 않는다."""
        try:
            existing = self.load()
            replaced = False
            for i, r in enumerate(existing):
                if r.id == record.id:
                    existing[i] = record
                    replaced = True
                    break
            if not replaced:
                existing.append(record)
            self.save_all(existing)
        except OSError as exc:
            logger.warning("Failed to update dreaming run record %s: %s", record.id, exc)

    def begin(
        self,
        *,
        input_msg_count: int = 0,
        started_at: datetime | None = None,
    ) -> DreamingRunRecord:
        """새 회차를 시작 — '진행 중' 행을 즉시 디스크에 남긴다.

        시작 시점에 행이 있으면 데몬이 사이클 도중 SIGKILL 등으로 죽어도 운영자가
        "마지막 시도 시각" 을 알 수 있다(그렇지 않으면 "왜 안 돌았지" 와 구분 불가).
        """
        rec = DreamingRunRecord(
            started_at=started_at or datetime.now(),
            input_msg_count=int(input_msg_count or 0),
        )
        self.append(rec)
        return rec

    def finish(
        self,
        record: DreamingRunRecord,
        *,
        ended_at: datetime | None = None,
        input_msg_count: int | None = None,
        generated_insight_count: int | None = None,
        rejected_count: int | None = None,
        error: str | None = None,
        skip_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DreamingRunRecord:
        """회차 종료 — 디스크 행을 갱신.

        ``error`` 와 ``skip_reason`` 은 상호 배타적이다(둘 다 채우면 ``skip_reason`` 이
        무시되고 error 로 분류된다 — 더 강한 시그널을 우선).
        ``skip_reason`` 이 ``VALID_SKIP_REASONS`` 에 없으면 WARN 로그 후 그대로 저장
        (포워드 호환). 호출부 오타 방지는 모듈 상단 상수로 강제한다.
        """
        record.ended_at = ended_at or datetime.now()
        if input_msg_count is not None:
            record.input_msg_count = int(input_msg_count)
        if generated_insight_count is not None:
            record.generated_insight_count = int(generated_insight_count)
        if rejected_count is not None:
            record.rejected_count = int(rejected_count)
        if error:
            record.error = error
            record.skip_reason = None
        elif skip_reason:
            if skip_reason not in VALID_SKIP_REASONS:
                logger.warning(
                    "Unknown dreaming skip_reason %r — accepted but should be defined in dreaming_runs.VALID_SKIP_REASONS",
                    skip_reason,
                )
            record.skip_reason = skip_reason
        if details:
            # 기존 details 와 병합 (begin 시 미리 채워둔 컨텍스트 보존).
            merged = dict(record.details)
            merged.update(details)
            record.details = merged
        self.update(record)
        return record

    # ---- 조회 / KPI 계산 ---------------------------------------------------

    def list_recent(self, limit: int = 20) -> list[DreamingRunRecord]:
        """최신 ``limit`` 건을 최신 → 오래된 순으로 반환. Admin UI 의 'recent N' 표 입력."""
        all_rows = self.load()
        if limit <= 0:
            return list(reversed(all_rows))
        return list(reversed(all_rows[-limit:]))

    def last_run(self) -> DreamingRunRecord | None:
        """가장 최근 회차(상태 무관). 없으면 None."""
        rows = self.load()
        return rows[-1] if rows else None

    def last_successful_run(self) -> DreamingRunRecord | None:
        """가장 최근 *성공* 회차(에러/스킵 아님). 없으면 None."""
        for rec in reversed(self.load()):
            if rec.status == "success":
                return rec
        return None

    def kpi_window(
        self,
        *,
        days: int = 7,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """지정 윈도우(기본 7일) KPI 집계.

        Returns:
            ``{"window_days", "total_runs", "success", "skip", "error",
              "input_msg_total", "insight_total", "rejected_total",
              "skip_breakdown": {reason: count}}``

            윈도우에 회차가 한 건도 없으면 모든 카운트가 0.
        """
        n = now or datetime.now()
        cutoff = n - timedelta(days=max(0, int(days)))
        rows = [r for r in self.load() if r.started_at >= cutoff]
        skip_counter: Counter[str] = Counter()
        success = skip = error = 0
        input_total = insight_total = rejected_total = 0
        for r in rows:
            if r.status == "success":
                success += 1
            elif r.status == "skip":
                skip += 1
                if r.skip_reason:
                    skip_counter[r.skip_reason] += 1
            elif r.status == "error":
                error += 1
            input_total += r.input_msg_count
            insight_total += r.generated_insight_count
            rejected_total += r.rejected_count
        return {
            "window_days": int(days),
            "total_runs": len(rows),
            "success": success,
            "skip": skip,
            "error": error,
            "input_msg_total": input_total,
            "insight_total": insight_total,
            "rejected_total": rejected_total,
            "skip_breakdown": dict(skip_counter),
        }


__all__ = [
    "DreamingRunRecord",
    "DreamingRunStore",
    "SKIP_NO_MESSAGES",
    "SKIP_PREFLIGHT_FAILED",
    "SKIP_MIDWRITE_ABORTED",
    "SKIP_EMPTY_RESULTS",
    "VALID_SKIP_REASONS",
]
