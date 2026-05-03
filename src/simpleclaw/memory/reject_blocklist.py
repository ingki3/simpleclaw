"""사용자가 reject 한 인사이트의 재학습 차단 리스트 (BIZ-78).

배경:
    사용자가 *틀렸다* 고 거부한 인사이트는 단순히 *현재 sidecar 에서만* 제거하면
    안 된다 — 다음 회차 LLM 추출이 같은 topic 을 또 뽑아내면 그 정정이 무효화된다.
    "거부했다" 는 신호 자체를 영속화하여 이후 모든 회차의 추출을 *원천 차단* 해야
    교정의 효과가 누적된다.

저장소 모델:
    - JSONL 한 줄당 한 항목 (``RejectEntry.to_dict``).
    - 키는 정규형 topic (``insights.normalize_topic``) — 사용자가 "맥북에어 가격 / 맥북에어가격"
      어느 형태로 보내도 동일 키로 매칭.
    - ``insights.jsonl`` 옆 ``rejects.jsonl`` 에 저장 — 두 파일 모두 ``.agent/`` 한 곳에 모이도록.

만료 정책:
    - ``ttl_seconds == None`` → 영구 차단 (가장 흔한 케이스: "이건 그냥 틀렸음").
    - ``ttl_seconds > 0`` → ``rejected_at + ttl`` 이 지나면 자동 해제 — "지금은 관심 없으나
      나중에는 모름" 같은 시한부 거부 케이스. ``daemon.dreaming.reject_blocklist.default_ttl_days``
      가 기본 TTL 을 결정하고, 운영자/Admin Review Loop(H) 가 항목별로 override 가능.

설계 결정:
    - 별도 sqlite 테이블이 아닌 파일 — insights.jsonl 과 라이프사이클을 맞추고 grep/diff 가능.
    - sidecar 와 마찬가지로 atomic rename 으로 부분 쓰기 손상 방지.
    - 정규화 책임은 ``insights.normalize_topic`` 에 위임 (단일 진실 공급원).
    - 만료 항목은 ``load(now=...)`` 가 자동 정리 — 호출자가 신경 쓸 필요 없음.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from simpleclaw.memory.insights import normalize_topic

logger = logging.getLogger(__name__)


@dataclass
class RejectEntry:
    """reject 된 topic 한 건의 메타.

    Attributes:
        topic: 사용자 표시용 원문 topic (정규화 전). 정규형은 키로만 사용.
        rejected_at: 거부된 시각.
        scope: 차단 범위. 현재 ``"global"`` 만 의미 있음(향후 H/Admin Review Loop 가
            세션·채널 등 좁은 scope 를 추가할 여지). 기본 ``"global"``.
        ttl_seconds: 차단 지속 시간(초). ``None`` 이면 영구. 0/음수 입력은 ``None`` 으로 정규화.
        reason: 운영자/사용자가 남긴 거부 사유 텍스트(자유 형식). Admin UI 검수용.
    """

    topic: str
    rejected_at: datetime = field(default_factory=datetime.now)
    scope: str = "global"
    ttl_seconds: int | None = None
    reason: str = ""

    def is_expired(self, now: datetime | None = None) -> bool:
        """``rejected_at + ttl`` 이 ``now`` 이전이면 True. ttl=None 이면 항상 False."""
        if self.ttl_seconds is None:
            return False
        if self.ttl_seconds <= 0:
            # 0/음수 TTL 은 무의미 — 안전하게 영구 차단으로 해석한다(절대 만료 안 됨).
            return False
        cutoff = self.rejected_at + timedelta(seconds=self.ttl_seconds)
        return (now or datetime.now()) >= cutoff

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "rejected_at": self.rejected_at.isoformat(),
            "scope": self.scope,
            "ttl_seconds": self.ttl_seconds,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RejectEntry:
        rejected_at_raw = d.get("rejected_at")
        try:
            rejected_at = (
                datetime.fromisoformat(rejected_at_raw)
                if isinstance(rejected_at_raw, str)
                else datetime.now()
            )
        except ValueError:
            rejected_at = datetime.now()
        ttl_raw = d.get("ttl_seconds")
        ttl_seconds: int | None
        if ttl_raw is None:
            ttl_seconds = None
        else:
            try:
                ttl_seconds = int(ttl_raw)
                if ttl_seconds <= 0:
                    ttl_seconds = None
            except (TypeError, ValueError):
                ttl_seconds = None
        return cls(
            topic=str(d.get("topic", "")),
            rejected_at=rejected_at,
            scope=str(d.get("scope") or "global"),
            ttl_seconds=ttl_seconds,
            reason=str(d.get("reason") or ""),
        )


class RejectBlocklistStore:
    """JSONL 기반 reject 차단 리스트 저장소.

    동시 쓰기는 가정하지 않는다(드리밍은 단일 사이클). load → mutate → save_all 이
    표준 사용 패턴이며, 만료된 항목은 ``load(now=...)`` 호출 시 자동으로 sweep 된다.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self, now: datetime | None = None) -> dict[str, RejectEntry]:
        """파일에서 차단 항목을 로드. 만료된 항목은 자동 제외(파일은 즉시 정리되지 않음).

        Returns:
            ``정규형 topic → RejectEntry`` 딕셔너리. 파일이 없으면 빈 dict.

        Note:
            만료 sweep 으로 발생한 *논리적* 변경은 다음 ``save_all`` 호출 때 디스크에
            반영된다. 호출자가 명시적으로 sweep 결과를 영속화하고 싶다면
            ``sweep_expired`` 를 사용하는 게 더 명확하다.
        """
        out: dict[str, RejectEntry] = {}
        if not self._path.is_file():
            return out
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read reject blocklist %s: %s", self._path, exc)
            return out
        n = now or datetime.now()
        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entry = RejectEntry.from_dict(d)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed reject line %d in %s: %s",
                    line_no, self._path, exc,
                )
                continue
            key = normalize_topic(entry.topic)
            if not key:
                continue
            if entry.is_expired(n):
                # 만료 항목은 메모리상에 노출하지 않는다. 파일 정리는 sweep_expired/save_all
                # 가 다음에 호출될 때 일어난다(읽기는 부작용이 없어야 한다는 원칙).
                continue
            out[key] = entry
        return out

    def save_all(self, entries: dict[str, RejectEntry]) -> None:
        """모든 항목을 JSONL 로 원자적으로 다시 쓴다."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for entry in entries.values():
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def add(
        self,
        topic: str,
        scope: str = "global",
        ttl_seconds: int | None = None,
        reason: str = "",
        now: datetime | None = None,
    ) -> RejectEntry:
        """topic 을 차단 리스트에 등록(중복 시 갱신). 정규형 키로 1행만 유지.

        같은 topic 을 다시 reject 하면 ``rejected_at`` 이 새로 갱신되고 ttl/reason 이
        교체된다 — "한 번 더 거부했다" 는 의미를 갖도록.

        Returns:
            저장된 ``RejectEntry``.
        """
        n = now or datetime.now()
        topic_norm = topic.strip()
        if not topic_norm or not normalize_topic(topic_norm):
            raise ValueError(f"empty topic cannot be blocklisted: {topic!r}")

        # ttl_seconds 정규화 — None / >0 만 의미 있음.
        if ttl_seconds is not None:
            try:
                ttl_seconds = int(ttl_seconds)
                if ttl_seconds <= 0:
                    ttl_seconds = None
            except (TypeError, ValueError):
                ttl_seconds = None

        existing = self.load(now=n)
        entry = RejectEntry(
            topic=topic_norm,
            rejected_at=n,
            scope=scope or "global",
            ttl_seconds=ttl_seconds,
            reason=reason or "",
        )
        existing[normalize_topic(topic_norm)] = entry
        self.save_all(existing)
        return entry

    def remove(self, topic: str) -> bool:
        """차단 리스트에서 topic 을 제거. 제거된 게 있으면 True.

        Admin UI 가 "거부 취소" 를 누를 때 호출된다 — 다음 회차부터 같은 topic 의
        추출이 다시 허용된다.
        """
        existing = self.load()
        key = normalize_topic(topic)
        if not key or key not in existing:
            return False
        existing.pop(key, None)
        self.save_all(existing)
        return True

    def is_blocked(self, topic: str, now: datetime | None = None) -> bool:
        """topic 이 현재 시점에 차단되어 있는지 여부."""
        key = normalize_topic(topic)
        if not key:
            return False
        return key in self.load(now=now)

    def sweep_expired(self, now: datetime | None = None) -> int:
        """만료된 항목을 디스크에서 영속 제거. 제거된 행 수 반환.

        ``load`` 는 만료 항목을 자동으로 메모리에서 빼지만 파일은 그대로 두기 때문에,
        주기적인 정리가 필요한 운영자 후크용으로 분리한다.
        """
        n = now or datetime.now()
        if not self._path.is_file():
            return 0
        # 만료되지 않은 항목만 수집해 다시 쓴다.
        keep = self.load(now=n)
        # load() 는 raw 파일에서 만료된 행을 메모리에서 제외해주지만,
        # 영속 제거를 보장하려면 파일을 다시 써야 한다. 제거 카운트는 파일에서 읽은
        # 총 라인 수와 keep 수의 차이로 계산.
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return 0
        nonempty_lines = [ln for ln in raw.splitlines() if ln.strip()]
        removed = len(nonempty_lines) - len(keep)
        if removed > 0:
            self.save_all(keep)
        return max(0, removed)
