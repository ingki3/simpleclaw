"""Dreaming suggestion queue + reject blocklist (BIZ-79).

BIZ-66 §3 Theme H — "Dry-run + Admin Review Loop".

dreaming 파이프라인이 추출한 모든 인사이트는 기본적으로 "제안(pending)" 상태로
이 큐에 저장된다. 자동 승격 임계(`auto_promote_confidence` AND
`auto_promote_evidence_count`) 를 동시에 충족하는 항목만 즉시 USER.md/MEMORY.md
managed 섹션에 반영되고, 나머지는 운영자(Admin UI)가 accept/edit/reject 할 때까지
큐에 남아 있다.

설계 결정:
- ``insights.jsonl`` (BIZ-73) 와 분리된 별도 sidecar(``insight_suggestions.jsonl``).
  - sidecar 의 데이터 라이프사이클이 다르다: insights 는 누적 메타, suggestions 는
    "검수 대기" 상태로 단명한다.
  - 같은 topic 이 sidecar 에선 evidence 누적되고 suggestions 에선 별도 행 — 운영자가
    회차별로 검수할 수 있도록.
- 같은 topic 이 두 회차에 들어오면 기존 pending suggestion 의 evidence/source 만
  업데이트한다 (UI 에선 한 줄로 보이는 게 자연스럽다). 신규 회차의 인사이트가 이미
  자동 승격된 경우엔 큐에 기록하지 않는다 — 큐는 "검수 대기"의 의미만.
- reject 시 ``InsightBlocklist`` 에 topic 을 추가한다(BIZ-78 의 영구 blocklist 원형).
  다음 사이클에서 같은 topic 의 관측이 들어오면 dreaming 이 sidecar 진입 자체를 차단.
- accept/edit 는 즉시 ``insights.jsonl`` 본 저장소에 ``confidence=1.0`` 으로 반영하고
  USER.md 섹션 갱신은 호출자(Admin API)가 ``DreamingPipeline.update_user_file`` 로 처리.

JSONL 포맷:
    {"topic": "맥북에어가격", "text": "...", "evidence_count": 1, "confidence": 0.4,
     "first_seen": "...", "last_seen": "...", "source_msg_ids": [12,13],
     "start_msg_id": 12, "end_msg_id": 13, "status": "pending",
     "suggested_at": "..."}
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.insights import InsightMeta, normalize_topic

logger = logging.getLogger(__name__)


# 큐 상태값. UI 가 필터에 사용한다. 기본은 pending.
SUGGESTION_STATUS_PENDING = "pending"
SUGGESTION_STATUS_ACCEPTED = "accepted"
SUGGESTION_STATUS_REJECTED = "rejected"


@dataclass
class SuggestionItem:
    """단일 dreaming 제안.

    ``InsightMeta`` 의 모든 필드 + 큐 상태 메타. ``InsightMeta`` 자체를 그대로
    embedding 하지 않고 평탄화하는 이유는, 행 단위로 ``status``/``suggested_at``
    같은 큐 메타를 ``jq`` 로 쉽게 필터하기 위함.
    """

    topic: str
    text: str
    evidence_count: int = 1
    confidence: float = 0.4
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    source_msg_ids: list[int] = field(default_factory=list)
    start_msg_id: int | None = None
    end_msg_id: int | None = None
    status: str = SUGGESTION_STATUS_PENDING
    suggested_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_insight(cls, meta: InsightMeta) -> SuggestionItem:
        """``InsightMeta`` 1건을 큐 항목으로 변환."""
        return cls(
            topic=meta.topic,
            text=meta.text,
            evidence_count=meta.evidence_count,
            confidence=meta.confidence,
            first_seen=meta.first_seen,
            last_seen=meta.last_seen,
            source_msg_ids=list(meta.source_msg_ids),
            start_msg_id=meta.start_msg_id,
            end_msg_id=meta.end_msg_id,
            status=SUGGESTION_STATUS_PENDING,
            suggested_at=datetime.now(),
        )

    def to_insight(self) -> InsightMeta:
        """큐 항목을 본 sidecar(``insights.jsonl``) 진입용 ``InsightMeta`` 로 변환.

        accept/edit 처리 시 ``InsightStore`` 에 저장하기 위해 사용된다.
        """
        return InsightMeta(
            topic=self.topic,
            text=self.text,
            evidence_count=self.evidence_count,
            confidence=self.confidence,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            source_msg_ids=list(self.source_msg_ids),
            start_msg_id=self.start_msg_id,
            end_msg_id=self.end_msg_id,
        )

    def to_dict(self) -> dict:
        """JSONL 직렬화 (datetime → ISO 문자열)."""
        d = asdict(self)
        d["first_seen"] = self.first_seen.isoformat()
        d["last_seen"] = self.last_seen.isoformat()
        d["suggested_at"] = self.suggested_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SuggestionItem:
        """JSONL 역직렬화. 누락 필드는 합리적 기본값."""
        first_seen = d.get("first_seen")
        last_seen = d.get("last_seen")
        suggested_at = d.get("suggested_at")
        return cls(
            topic=str(d.get("topic", "")),
            text=str(d.get("text", "")),
            evidence_count=int(d.get("evidence_count", 1)),
            confidence=float(d.get("confidence", 0.4)),
            first_seen=(
                datetime.fromisoformat(first_seen)
                if isinstance(first_seen, str)
                else datetime.now()
            ),
            last_seen=(
                datetime.fromisoformat(last_seen)
                if isinstance(last_seen, str)
                else datetime.now()
            ),
            source_msg_ids=list(d.get("source_msg_ids") or []),
            start_msg_id=(
                int(d["start_msg_id"]) if d.get("start_msg_id") is not None else None
            ),
            end_msg_id=(
                int(d["end_msg_id"]) if d.get("end_msg_id") is not None else None
            ),
            status=str(d.get("status", SUGGESTION_STATUS_PENDING)),
            suggested_at=(
                datetime.fromisoformat(suggested_at)
                if isinstance(suggested_at, str)
                else datetime.now()
            ),
        )


class SuggestionStore:
    """JSONL 기반 dreaming 제안 큐.

    파일 구조: 한 줄당 ``SuggestionItem.to_dict()`` JSON. topic 정규형이 unique key.
    rejected/accepted 항목은 audit 보존을 위해 큐에 그대로 남기되, ``status`` 만
    바뀐다(UI 는 status='pending' 만 기본 노출).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, SuggestionItem]:
        """파일에서 모든 제안을 로드. topic 정규형을 키로 한다."""
        out: dict[str, SuggestionItem] = {}
        if not self._path.is_file():
            return out
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read suggestion sidecar %s: %s", self._path, exc)
            return out

        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                item = SuggestionItem.from_dict(d)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed suggestion line %d in %s: %s",
                    line_no, self._path, exc,
                )
                continue
            key = normalize_topic(item.topic)
            if not key:
                continue
            out[key] = item
        return out

    def save_all(self, items: dict[str, SuggestionItem]) -> None:
        """전량을 atomic-rename 으로 다시 쓴다.

        ``InsightStore.save_all`` 과 동일 패턴 — 부분 쓰기 중 크래시로 손상 방지.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for item in items.values():
                f.write(json.dumps(item.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def list_pending(self) -> list[SuggestionItem]:
        """검수 대기(pending) 항목만 정렬하여 반환 (suggested_at 내림차순).

        UI 에서 \"가장 최근 제안 먼저\" 보여주는 게 운영 직관에 맞는다.
        """
        items = [
            item
            for item in self.load().values()
            if item.status == SUGGESTION_STATUS_PENDING
        ]
        items.sort(key=lambda i: i.suggested_at, reverse=True)
        return items

    def find_by_topic(self, topic: str) -> SuggestionItem | None:
        """topic(원문 또는 정규형)으로 1건 조회."""
        key = normalize_topic(topic)
        if not key:
            return None
        return self.load().get(key)

    def upsert_pending(self, item: SuggestionItem) -> None:
        """pending 상태로 1건 upsert. 같은 topic 의 기존 pending 은 덮어쓴다.

        accepted/rejected 상태의 행이 이미 존재하면 그 상태를 유지한다 — 한 번 결정된
        제안이 같은 회차로 다시 들어와도 운영자 결정을 무시하지 않기 위함.
        """
        key = normalize_topic(item.topic)
        if not key:
            return
        items = self.load()
        existing = items.get(key)
        if existing is not None and existing.status != SUGGESTION_STATUS_PENDING:
            # accepted/rejected 결정은 보존. evidence/source 만 누적해 audit 풍부화.
            existing.evidence_count = item.evidence_count
            existing.confidence = item.confidence
            existing.last_seen = item.last_seen
            existing.source_msg_ids = list(item.source_msg_ids)
            existing.start_msg_id = item.start_msg_id
            existing.end_msg_id = item.end_msg_id
            items[key] = existing
        else:
            item.status = SUGGESTION_STATUS_PENDING
            items[key] = item
        self.save_all(items)

    def mark_status(self, topic: str, status: str) -> SuggestionItem | None:
        """주어진 topic 의 상태를 변경(accept/reject 처리). 없으면 None."""
        key = normalize_topic(topic)
        if not key:
            return None
        items = self.load()
        item = items.get(key)
        if item is None:
            return None
        item.status = status
        items[key] = item
        self.save_all(items)
        return item

    def update_text(self, topic: str, new_text: str) -> SuggestionItem | None:
        """edit 액션용 — 본문만 새 값으로 갱신(상태는 그대로). 없으면 None."""
        key = normalize_topic(topic)
        if not key:
            return None
        items = self.load()
        item = items.get(key)
        if item is None:
            return None
        item.text = new_text
        items[key] = item
        self.save_all(items)
        return item


class InsightBlocklist:
    """JSONL 기반 reject blocklist (BIZ-78 의 stub).

    한 줄당 한 항목: ``{"topic": "...", "blocked_at": "...", "reason": "..."}``.
    드리밍 사이클에서 LLM 이 추출한 인사이트 중 blocklist 에 있는 topic 은 sidecar
    진입 단계에서 즉시 drop 된다. 운영자가 한 번 reject 한 인사이트가 다음 사이클에
    같은 형태로 다시 나타나는 \"좀비 인사이트\" 문제를 막는다.

    BIZ-78 (Theme C: Decay & Re-evaluation) 가 머지되면 본 stub 이 그쪽 모듈로
    승격되거나 forwarder 로 남는다. 그때까지 BIZ-79 운영을 막지 않기 위한 최소 구현.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not self._path.is_file():
            return out
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read blocklist %s: %s", self._path, exc)
            return out
        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Skipping malformed blocklist line %d in %s: %s",
                    line_no, self._path, exc,
                )
                continue
            topic = str(d.get("topic", "")).strip()
            if not topic:
                continue
            out[normalize_topic(topic)] = d
        return out

    def is_blocked(self, topic: str) -> bool:
        key = normalize_topic(topic)
        if not key:
            return False
        return key in self._load()

    def add(self, topic: str, *, reason: str = "user_rejected") -> None:
        """topic 을 blocklist 에 추가(이미 있으면 timestamp 갱신).

        atomic-rename 으로 전량 재기록 — 동시 쓰기는 dreaming + admin reject 두 경로뿐이며
        실제 동시성은 거의 발생하지 않지만 손상 방지.
        """
        key = normalize_topic(topic)
        if not key:
            return
        entries = self._load()
        entries[key] = {
            "topic": topic.strip(),
            "blocked_at": datetime.now().isoformat(),
            "reason": reason,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for entry in entries.values():
                f.write(json.dumps(entry, ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def list_topics(self) -> list[dict]:
        """blocklist 전체를 dict 리스트로 반환 (UI/디버그용)."""
        return list(self._load().values())


# ----------------------------------------------------------------------
# 자동 승격 정책
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class AutoPromotePolicy:
    """제안 → 자동 승격 결정 정책 (config 로 노출).

    DoD §B 의 \"confidence ≥ X 그리고 evidence_count ≥ Y 동시 충족\" 표현.
    두 조건을 AND 로 결합 — 한 쪽만 충족하면 큐에 남는다.
    """

    confidence: float = 0.7
    evidence_count: int = 3

    def should_auto_promote(self, meta: InsightMeta) -> bool:
        return (
            meta.confidence >= self.confidence
            and meta.evidence_count >= self.evidence_count
        )
