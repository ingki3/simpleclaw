"""대화 이력을 SQLite 데이터베이스에 저장하고 조회하는 모듈.

주요 동작 흐름:
1. ConversationStore 인스턴스 생성 시 SQLite DB 파일 경로를 받아 스키마를 자동 생성·마이그레이션한다.
2. add_message()로 대화 메시지를 순차 저장하며, INSERT된 행 id를 반환한다.
3. get_recent() / get_since()로 최근 또는 특정 시점 이후 대화를 시간순으로 조회한다.
   /undo로 soft-delete된 메시지는 기본 컨텍스트 조회에서 제외한다.
4. add_embedding() / search_similar()로 메시지 단위 임베딩을 저장·검색한다(시맨틱 메모리, spec 005).
5. create_cluster() / list_clusters() / assign_cluster() 등으로 시맨틱 클러스터 그래프를 관리한다(Phase 3).
6. create_memory_item() 계열 CRUD로 DB-backed 장기기억 read model을 관리한다(BIZ-307).

설계 결정:
- 각 메서드 호출마다 ``_connect()`` helper를 통해 SQLite 연결을 열고,
  transaction context 종료 뒤 반드시 ``close()``한다. Python sqlite3 연결
  context manager는 commit/rollback만 수행하고 close하지 않으므로 장기 실행
  프로세스에서 DB 파일 FD가 누적되지 않게 명시 close가 필요하다.
- 메시지 순서는 auto-increment id 기준이며, timestamp는 보조 필터로만 사용한다.
- 임베딩은 float32 little-endian 연속 BLOB로 저장한다(numpy.tobytes 직렬화).
  Phase 1에서는 코사인 유사도를 인메모리(numpy)로 계산한다. 메시지 수가 1만 이상으로
  커질 경우 sqlite-vec 가상 테이블 도입을 Phase 2/3에서 검토한다.
- 저널 모드는 WAL로 설정하여 데몬·드리밍 동시 쓰기 시 잠금 충돌을 줄인다.
- 기존 행에 embedding 컬럼이 없을 때 자동으로 ALTER TABLE 마이그레이션한다(멱등).
- Phase 3에서 ``semantic_clusters`` 테이블을 추가하고 ``messages.cluster_id`` 컬럼으로
  메시지를 클러스터에 부착한다. cluster_id는 외래 키 제약 없이 정수 참조로만 둔다
  (클러스터 삭제 시 멤버 메시지는 보존하되 cluster_id가 dangling 상태로 남는다 — 무시).
- BIZ-307 ``memory_items``는 MEMORY.md/USER.md 파서를 즉시 대체하지 않는 additive
  read model이다. source_msg_ids/metadata는 JSON TEXT로 저장해 SQLite 의존성을 유지한다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np

from simpleclaw.db import run_conversations_migrations
from simpleclaw.memory.models import (
    ClusterRecord,
    ConversationMessage,
    MemoryItem,
    MemoryItemStatus,
    MemoryItemType,
    MessageRole,
)

logger = logging.getLogger(__name__)

# 임베딩 BLOB 직렬화 dtype. e5-small 등 대부분의 문장 임베딩 모델은 float32로 충분하다.
# 차원은 호출자가 일관되게 관리하며 저장소는 강제하지 않는다.
_EMBEDDING_DTYPE = np.float32


class ConversationStore:
    """대화 이력을 로컬 SQLite 데이터베이스에 저장하고 조회하는 저장소.

    인스턴스 생성 시 DB 파일이 없으면 자동으로 생성하며,
    messages 테이블 스키마와 임베딩 컬럼 마이그레이션도 함께 수행한다.
    """

    def __init__(self, db_path: str | Path) -> None:
        """대화 저장소를 초기화한다.

        Args:
            db_path: SQLite 데이터베이스 파일 경로. 존재하지 않으면 새로 생성된다.
        """
        self._db_path = str(db_path)
        self._ensure_schema()

    def close(self) -> None:
        """저장소 종료 hook.

        현재 구현은 메서드 호출마다 SQLite 연결을 열고 닫으므로 인스턴스가 보유한
        장기 연결은 없다. 그래도 fixture/호출자가 ``close()`` 또는 context manager를
        일관되게 사용할 수 있도록 idempotent no-op으로 명시한다.
        """

    def __enter__(self) -> "ConversationStore":
        """``with ConversationStore(...)`` 패턴에서 저장소 자신을 반환한다."""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """context manager 종료 시 close 정책을 한곳으로 모은다."""
        self.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """SQLite 연결의 transaction 처리와 close를 한곳에서 보장한다.

        ``sqlite3.Connection`` 자체의 context manager는 commit/rollback만 수행하고
        파일 디스크립터를 닫지 않는다. 장기 실행 봇에서는 작은 누락도 FD leak로
        누적되므로, 모든 DB 접근은 이 helper를 통해 close까지 명시적으로 수행한다.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """레거시 컬럼 정규화 후 마이그레이션 러너에 위임해 스키마를 최신화한다.

        설계 결정:
        - 0001_initial.sql이 이전에 in-line으로 생성하던 messages/semantic_clusters
          테이블을 모두 포함한다. 이후 컬럼·인덱스 변경은 0002_*.sql 이후로 분리한다.
        - **레거시 정규화**: spec 005 도입 이전(Phase 1 이하) DB는 messages 테이블에
          embedding/cluster_id 컬럼이 없을 수 있다. 마이그레이션 러너의 베이스라인
          흡수는 테이블 존재만 검사하므로, 컬럼 누락 상태로 흡수되면 영원히
          누락된 채 남는다. 이를 막기 위해 러너 호출 전에 PRAGMA table_info로
          누락 컬럼만 ALTER TABLE로 보충한다(데이터 보존, 멱등).
        - 마이그레이션 적용 중 실패하면 러너가 백업 파일에서 DB를 원복하고
          MigrationError를 raise한다. 호출자(__init__)가 이 예외를 그대로 전파해
          부팅 실패를 명확히 한다.
        """
        self._normalize_legacy_columns()
        run_conversations_migrations(self._db_path)

    def _normalize_legacy_columns(self) -> None:
        """spec 005 이전 DB의 messages 테이블에 누락 컬럼을 추가한다.

        이미 모든 컬럼이 있거나 messages 테이블이 없으면 아무 일도 하지 않는다.
        러너 호출 전 1회 실행으로 충분하며, 신규 DB에는 영향이 없다.
        """
        if not Path(self._db_path).exists():
            return
        with self._connect() as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "messages" not in tables:
                return
            cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(messages)"
                ).fetchall()
            }
            if "embedding" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN embedding BLOB")
                logger.info("Legacy normalize: added embedding column to messages")
            if "cluster_id" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN cluster_id INTEGER")
                logger.info("Legacy normalize: added cluster_id column to messages")
            # BIZ-77/BIZ-366 — channel/deleted_at 컬럼은 각각 마이그레이션이 추가한다.
            # 여기서 보충하면 해당 ALTER 가 duplicate 로 실패하므로, 정규화는
            # baseline (embedding/cluster_id) 까지만 처리하고 이후 컬럼은 마이그레이션
            # 러너에 위임한다.

    @staticmethod
    def _message_from_row(row: tuple) -> ConversationMessage:
        """messages SELECT row를 ConversationMessage로 변환한다.

        soft-delete 메타(deleted_at)는 컨텍스트 노출 여부만 제어하므로 도메인 메시지
        객체에는 싣지 않는다. 감사 조회는 row id를 함께 반환하는 API가 담당한다.
        """
        role, content, ts, tokens, channel = row
        return ConversationMessage(
            role=MessageRole(role),
            content=content,
            timestamp=datetime.fromisoformat(ts),
            token_count=tokens,
            channel=channel,
        )

    def add_message(self, message: ConversationMessage) -> int:
        """새 대화 메시지를 DB에 저장하고 INSERT된 행 id를 반환한다.

        반환 id는 이후 ``add_embedding()`` 호출 시 message_id로 사용한다.
        기존 호출자는 반환값을 무시해도 무방하다(이전 시그니처 호환).

        ``message.channel`` 은 BIZ-77 에서 추가된 선택 컬럼이며 ``None`` 이면
        DB에 NULL 로 저장된다. producer (텔레그램/웹훅/cron 등)가 명시하지 않으면
        Admin UI 에서는 "unknown" 으로 노출된다.

        Args:
            message: 저장할 대화 메시지 객체.

        Returns:
            INSERT된 messages 행의 id.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (role, content, timestamp, token_count, channel) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    message.role.value,
                    message.content,
                    message.timestamp.isoformat(),
                    message.token_count,
                    message.channel,
                ),
            )
            return int(cursor.lastrowid)

    def get_recent(
        self, limit: int = 20, *, include_deleted: bool = False
    ) -> list[ConversationMessage]:
        """최근 메시지를 시간순으로 반환한다.

        Args:
            limit: 가져올 최대 메시지 수. 기본값 20.
            include_deleted: True이면 /undo로 숨겨진 메시지도 감사용으로 포함한다.

        Returns:
            시간순(오래된 것 먼저)으로 정렬된 ConversationMessage 리스트.
        """
        visibility_clause = "" if include_deleted else "WHERE deleted_at IS NULL "
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count, channel FROM messages "
                f"{visibility_clause}ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        messages = []
        # DB에서 역순(최신 먼저)으로 가져온 뒤 다시 뒤집어 시간순으로 만든다
        for row in reversed(rows):
            messages.append(self._message_from_row(row))
        return messages

    def get_since(
        self, since: datetime, *, include_deleted: bool = False
    ) -> list[ConversationMessage]:
        """지정된 시점 이후의 메시지를 시간순으로 반환한다.

        Args:
            since: 이 시점 이후(포함)의 메시지를 조회한다.

        Returns:
            시간순으로 정렬된 ConversationMessage 리스트.
        """
        deleted_clause = "" if include_deleted else "AND deleted_at IS NULL "
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count, channel FROM messages "
                f"WHERE timestamp >= ? {deleted_clause}ORDER BY id",
                (since.isoformat(),),
            ).fetchall()

        return [self._message_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # ID-bearing fetch (BIZ-77) — 인사이트 source 역추적을 위한 헬퍼
    # ------------------------------------------------------------------

    def get_recent_with_ids(
        self, limit: int = 20, *, include_deleted: bool = False
    ) -> list[tuple[int, ConversationMessage]]:
        """최근 메시지를 ``(id, message)`` 쌍으로 시간순 반환.

        ``get_recent`` 와 동일하지만 message rowid 를 함께 노출한다 — 드리밍이
        인사이트 메타에 ``source_msg_ids`` 로 적재해야 하므로 BIZ-77 에서 추가됨.
        """
        visibility_clause = "" if include_deleted else "WHERE deleted_at IS NULL "
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, timestamp, token_count, channel "
                f"FROM messages {visibility_clause}ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results: list[tuple[int, ConversationMessage]] = []
        # DB에서 역순(최신 먼저)으로 가져온 뒤 다시 뒤집어 시간순으로 만든다.
        for mid, role, content, ts, tokens, channel in reversed(rows):
            msg = self._message_from_row((role, content, ts, tokens, channel))
            results.append((int(mid), msg))
        return results

    def get_since_with_ids(
        self, since: datetime, *, include_deleted: bool = False
    ) -> list[tuple[int, ConversationMessage]]:
        """지정 시점 이후 메시지를 ``(id, message)`` 쌍으로 시간순 반환."""
        deleted_clause = "" if include_deleted else "AND deleted_at IS NULL "
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, timestamp, token_count, channel "
                f"FROM messages WHERE timestamp >= ? {deleted_clause}ORDER BY id",
                (since.isoformat(),),
            ).fetchall()

        return [
            (
                int(mid),
                self._message_from_row((role, content, ts, tokens, channel)),
            )
            for mid, role, content, ts, tokens, channel in rows
        ]

    def get_messages_by_ids(
        self, ids: list[int], *, include_deleted: bool = True
    ) -> list[tuple[int, ConversationMessage]]:
        """주어진 rowid 들을 시간순으로 조회한다.

        존재하지 않는 id 는 결과에서 그냥 빠진다(에러 없음). 기본적으로 /undo로
        숨긴 메시지도 포함해 Admin/API 감사 경로가 물리 보존된 원문을 확인할 수 있게 한다.

        Args:
            ids: messages.id 리스트 (중복/빈 리스트 허용).

        Returns:
            id 오름차순(= 시간순) ``(id, ConversationMessage)`` 리스트.
        """
        if not ids:
            return []
        # 중복 제거 + 정렬 — sqlite IN ()가 빈 튜플을 허용하지 않으므로 가드.
        unique_ids = sorted({int(i) for i in ids})
        if not unique_ids:
            return []
        placeholders = ",".join("?" * len(unique_ids))
        deleted_clause = "" if include_deleted else "AND deleted_at IS NULL "
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, role, content, timestamp, token_count, channel "
                f"FROM messages WHERE id IN ({placeholders}) {deleted_clause}ORDER BY id",
                unique_ids,
            ).fetchall()

        return [
            (
                int(mid),
                self._message_from_row((role, content, ts, tokens, channel)),
            )
            for mid, role, content, ts, tokens, channel in rows
        ]

    def hide_recent_user_turns(self, turns: int) -> int:
        """최근 user turn N개와 그 이후 assistant 메시지를 soft-delete한다.

        물리 삭제 없이 ``deleted_at``만 채워 이후 ``get_recent``/``get_since`` 기본
        컨텍스트에서 제외한다. 최신 메시지부터 거꾸로 훑어 user 메시지 N개를 만날
        때까지 지나친 assistant/system/tool 성격의 행까지 같은 되돌림 범위로 묶는다.

        Args:
            turns: 숨길 최근 user turn 수. 1 이상의 정수여야 한다.

        Returns:
            실제 숨겨진 user turn 수. 숨길 user 메시지가 없으면 0.
        """
        if turns < 1:
            raise ValueError("turns must be >= 1")

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role FROM messages WHERE deleted_at IS NULL ORDER BY id DESC"
            ).fetchall()
            ids_to_hide: list[int] = []
            user_turns = 0
            for mid, role in rows:
                ids_to_hide.append(int(mid))
                if role == MessageRole.USER.value:
                    user_turns += 1
                    if user_turns >= turns:
                        break

            if user_turns == 0:
                return 0

            placeholders = ",".join("?" * len(ids_to_hide))
            conn.execute(
                f"UPDATE messages SET deleted_at = ? WHERE id IN ({placeholders})",
                [datetime.now().isoformat(), *ids_to_hide],
            )
            return user_turns

    def count(self) -> int:
        """저장된 전체 메시지 수를 반환한다."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


    # ------------------------------------------------------------------
    # DB-backed 장기기억 항목 (BIZ-307) — Phase 1 read model
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_memory_item_type(item_type: MemoryItemType | str) -> MemoryItemType:
        """외부 입력(enum 또는 문자열)을 MemoryItemType으로 정규화한다."""
        if isinstance(item_type, MemoryItemType):
            return item_type
        return MemoryItemType(str(item_type))

    @staticmethod
    def _coerce_memory_item_status(status: MemoryItemStatus | str) -> MemoryItemStatus:
        """외부 입력(enum 또는 문자열)을 MemoryItemStatus로 정규화한다."""
        if isinstance(status, MemoryItemStatus):
            return status
        return MemoryItemStatus(str(status))

    @staticmethod
    def _normalize_source_msg_ids(source_msg_ids: list[int] | None) -> list[int]:
        """근거 message id 목록을 중복 제거 + 오름차순 정렬한다."""
        if not source_msg_ids:
            return []
        return sorted({int(mid) for mid in source_msg_ids})

    @staticmethod
    def _decode_json_list(raw: str | None) -> list[int]:
        """JSON TEXT로 저장된 source_msg_ids를 안전하게 list[int]로 복원한다."""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out: list[int] = []
        for value in data:
            try:
                out.append(int(value))
            except (TypeError, ValueError):
                continue
        return sorted(set(out))

    @staticmethod
    def _decode_json_dict(raw: str | None) -> dict:
        """JSON TEXT로 저장된 metadata를 안전하게 dict로 복원한다."""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _decode_embedding(blob: bytes | None) -> np.ndarray | None:
        """memory_items.embedding BLOB을 float32 ndarray로 복원한다."""
        if blob is None:
            return None
        try:
            arr = np.frombuffer(blob, dtype=_EMBEDDING_DTYPE)
        except (TypeError, ValueError):
            return None
        return arr.copy() if arr.size else None

    @classmethod
    def _memory_item_from_row(cls, row: sqlite3.Row | tuple) -> MemoryItem:
        """SQLite row를 MemoryItem dataclass로 변환한다."""
        (
            item_id,
            item_type,
            text,
            source,
            source_ref,
            confidence,
            importance,
            status,
            first_seen,
            last_seen,
            last_accessed,
            embedding,
            created_at,
            updated_at,
            archived_at,
            source_msg_ids,
            metadata,
        ) = row
        return MemoryItem(
            id=int(item_id),
            type=MemoryItemType(item_type),
            text=text,
            source=source or "",
            source_ref=source_ref or "",
            confidence=float(confidence or 0.0),
            importance=float(importance or 0.0),
            status=MemoryItemStatus(status),
            first_seen=datetime.fromisoformat(first_seen),
            last_seen=datetime.fromisoformat(last_seen),
            last_accessed=(
                datetime.fromisoformat(last_accessed) if last_accessed else None
            ),
            embedding=cls._decode_embedding(embedding),
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
            archived_at=(
                datetime.fromisoformat(archived_at) if archived_at else None
            ),
            source_msg_ids=cls._decode_json_list(source_msg_ids),
            metadata=cls._decode_json_dict(metadata),
        )

    def create_memory_item(
        self,
        *,
        item_type: MemoryItemType | str,
        text: str,
        source: str = "",
        source_ref: str = "",
        confidence: float = 0.0,
        importance: float = 0.0,
        status: MemoryItemStatus | str = MemoryItemStatus.ACTIVE,
        first_seen: datetime | None = None,
        last_seen: datetime | None = None,
        embedding: Sequence[float] | np.ndarray | None = None,
        source_msg_ids: list[int] | None = None,
        metadata: dict | None = None,
    ) -> MemoryItem:
        """새 장기기억 항목을 생성하고 저장된 MemoryItem을 반환한다.

        Phase 1에서는 MEMORY.md/USER.md를 직접 갱신하지 않고, 후속 UI/API가 안정적인
        id로 참조할 수 있는 DB read model만 만든다.
        """
        normalized_type = self._coerce_memory_item_type(item_type)
        normalized_status = self._coerce_memory_item_status(status)
        now_iso = datetime.now().isoformat()
        first_seen_iso = (first_seen or datetime.now()).isoformat()
        last_seen_iso = (last_seen or first_seen or datetime.now()).isoformat()
        archived_at = now_iso if normalized_status is MemoryItemStatus.ARCHIVED else None
        msg_ids = self._normalize_source_msg_ids(source_msg_ids)
        embedding_blob = None
        if embedding is not None:
            arr = np.asarray(embedding, dtype=_EMBEDDING_DTYPE)
            if arr.ndim != 1 or arr.size == 0:
                raise ValueError("embedding must be a non-empty 1-D vector")
            embedding_blob = arr.tobytes()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO memory_items "
                "(type, text, source, source_ref, confidence, importance, status, "
                "first_seen, last_seen, last_accessed, embedding, created_at, updated_at, archived_at, "
                "source_msg_ids, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    normalized_type.value,
                    text,
                    source,
                    source_ref,
                    float(confidence),
                    float(importance),
                    normalized_status.value,
                    first_seen_iso,
                    last_seen_iso,
                    None,
                    embedding_blob,
                    now_iso,
                    now_iso,
                    archived_at,
                    json.dumps(msg_ids, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            item_id = int(cursor.lastrowid)
        item = self.get_memory_item(item_id)
        if item is None:
            raise ValueError(f"memory item {item_id} was not created")
        return item

    def get_memory_item_by_source(
        self, source: str, source_ref: str
    ) -> MemoryItem | None:
        """source/source_ref natural key로 장기기억 항목을 조회한다."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, type, text, source, source_ref, confidence, importance, status, "
                "first_seen, last_seen, last_accessed, embedding, created_at, updated_at, "
                "archived_at, source_msg_ids, metadata FROM memory_items "
                "WHERE source = ? AND source_ref = ? ORDER BY id LIMIT 1",
                (source, source_ref),
            ).fetchone()
        return self._memory_item_from_row(row) if row is not None else None

    def upsert_memory_item(
        self,
        *,
        item_type: MemoryItemType | str,
        text: str,
        source: str,
        source_ref: str,
        confidence: float = 0.0,
        importance: float = 0.0,
        status: MemoryItemStatus | str = MemoryItemStatus.ACTIVE,
        first_seen: datetime | None = None,
        last_seen: datetime | None = None,
        embedding: Sequence[float] | np.ndarray | None = None,
        source_msg_ids: list[int] | None = None,
        metadata: dict | None = None,
    ) -> MemoryItem:
        """source/source_ref 기준으로 memory_items를 멱등 생성 또는 갱신한다.

        embedding이 None이면 기존 embedding을 보존하고, 값이 제공될 때만 float32 BLOB로
        갱신한다. text/source/status/confidence/importance는 embedding 유무와 무관하게
        매 호출 최신값으로 반영한다.
        """
        if not source or not source_ref:
            raise ValueError("source and source_ref are required for upsert")
        existing = self.get_memory_item_by_source(source, source_ref)
        if existing is None:
            return self.create_memory_item(
                item_type=item_type,
                text=text,
                source=source,
                source_ref=source_ref,
                confidence=confidence,
                importance=importance,
                status=status,
                first_seen=first_seen,
                last_seen=last_seen,
                embedding=embedding,
                source_msg_ids=source_msg_ids,
                metadata=metadata,
            )
        return self.update_memory_item(
            existing.id,
            item_type=item_type,
            text=text,
            confidence=confidence,
            importance=importance,
            status=status,
            first_seen=first_seen,
            last_seen=last_seen,
            embedding=embedding,
            source_msg_ids=source_msg_ids,
            metadata=metadata,
        )

    def get_memory_item(self, item_id: int) -> MemoryItem | None:
        """id로 장기기억 항목을 단건 조회한다. 없으면 None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, type, text, source, source_ref, confidence, importance, status, "
                "first_seen, last_seen, last_accessed, embedding, created_at, updated_at, "
                "archived_at, source_msg_ids, metadata FROM memory_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        return self._memory_item_from_row(row) if row is not None else None

    def list_memory_items(
        self,
        *,
        item_type: MemoryItemType | str | None = None,
        status: MemoryItemStatus | str | None = None,
        source: str | None = None,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> list[MemoryItem]:
        """장기기억 항목을 필터링해 updated_at 내림차순으로 반환한다.

        ``include_archived=False``이고 명시 status가 없으면 active 항목만 반환한다.
        archive는 삭제가 아니므로 ``include_archived=True``로 전체 추적이 가능하다.
        """
        where: list[str] = []
        params: list = []
        if item_type is not None:
            where.append("type = ?")
            params.append(self._coerce_memory_item_type(item_type).value)
        if status is not None:
            where.append("status = ?")
            params.append(self._coerce_memory_item_status(status).value)
        elif not include_archived:
            where.append("status = ?")
            params.append(MemoryItemStatus.ACTIVE.value)
        if source is not None:
            where.append("source = ?")
            params.append(source)

        sql = (
            "SELECT id, type, text, source, source_ref, confidence, importance, status, "
            "first_seen, last_seen, last_accessed, embedding, created_at, updated_at, "
            "archived_at, source_msg_ids, metadata FROM memory_items"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._memory_item_from_row(row) for row in rows]

    def update_memory_item(
        self,
        item_id: int,
        *,
        item_type: MemoryItemType | str | None = None,
        text: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        status: MemoryItemStatus | str | None = None,
        first_seen: datetime | None = None,
        last_seen: datetime | None = None,
        last_accessed: datetime | None = None,
        embedding: Sequence[float] | np.ndarray | None = None,
        source_msg_ids: list[int] | None = None,
        metadata: dict | None = None,
    ) -> MemoryItem:
        """장기기억 항목을 부분 갱신하고 갱신된 MemoryItem을 반환한다.

        ``None``으로 둔 필드는 변경하지 않는다. status를 archived로 직접 넘기면
        archived_at도 함께 채우고, active로 되돌리면 archived_at을 비운다.
        """
        sets: list[str] = []
        params: list = []
        if item_type is not None:
            sets.append("type = ?")
            params.append(self._coerce_memory_item_type(item_type).value)
        if text is not None:
            sets.append("text = ?")
            params.append(text)
        if source is not None:
            sets.append("source = ?")
            params.append(source)
        if source_ref is not None:
            sets.append("source_ref = ?")
            params.append(source_ref)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(float(confidence))
        if importance is not None:
            sets.append("importance = ?")
            params.append(float(importance))
        if first_seen is not None:
            sets.append("first_seen = ?")
            params.append(first_seen.isoformat())
        if last_seen is not None:
            sets.append("last_seen = ?")
            params.append(last_seen.isoformat())
        if last_accessed is not None:
            sets.append("last_accessed = ?")
            params.append(last_accessed.isoformat())
        if embedding is not None:
            arr = np.asarray(embedding, dtype=_EMBEDDING_DTYPE)
            if arr.ndim != 1 or arr.size == 0:
                raise ValueError("embedding must be a non-empty 1-D vector")
            sets.append("embedding = ?")
            params.append(arr.tobytes())
        if status is not None:
            normalized_status = self._coerce_memory_item_status(status)
            sets.append("status = ?")
            params.append(normalized_status.value)
            sets.append("archived_at = ?")
            params.append(
                datetime.now().isoformat()
                if normalized_status is MemoryItemStatus.ARCHIVED
                else None
            )
        if source_msg_ids is not None:
            sets.append("source_msg_ids = ?")
            params.append(
                json.dumps(
                    self._normalize_source_msg_ids(source_msg_ids),
                    ensure_ascii=False,
                )
            )
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(json.dumps(metadata, ensure_ascii=False))
        if not sets:
            item = self.get_memory_item(item_id)
            if item is None:
                raise ValueError(f"memory_item_id {item_id} does not exist")
            return item

        sets.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(item_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE memory_items SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise ValueError(f"memory_item_id {item_id} does not exist")
        item = self.get_memory_item(item_id)
        if item is None:
            raise ValueError(f"memory_item_id {item_id} does not exist")
        return item

    def archive_memory_item(self, item_id: int) -> MemoryItem:
        """장기기억 항목을 삭제하지 않고 archived 상태로 전환한다."""
        return self.update_memory_item(item_id, status=MemoryItemStatus.ARCHIVED)

    def mark_memory_item_accessed(self, item_id: int) -> MemoryItem:
        """retrieval hit 후 last_accessed를 현재 시각으로 갱신한다."""
        return self.update_memory_item(item_id, last_accessed=datetime.now())

    def search_memory_items(
        self,
        query_vector: Sequence[float] | np.ndarray,
        k: int = 5,
        *,
        min_score: float = -1.0,
        min_confidence: float = 0.0,
    ) -> list[tuple[MemoryItem, float]]:
        """active memory_items를 embedding cosine similarity로 검색한다."""
        from simpleclaw.memory.supersession import memory_item_supersession_boost

        query = np.asarray(query_vector, dtype=_EMBEDDING_DTYPE)
        if query.ndim != 1 or query.size == 0:
            raise ValueError("query_vector must be a non-empty 1-D vector")
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0.0:
            raise ValueError("query_vector must not be a zero vector")
        query_unit = query / query_norm
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, type, text, source, source_ref, confidence, importance, status, "
                "first_seen, last_seen, last_accessed, embedding, created_at, updated_at, "
                "archived_at, source_msg_ids, metadata FROM memory_items "
                "WHERE status = ? AND confidence >= ? AND embedding IS NOT NULL",
                (MemoryItemStatus.ACTIVE.value, float(min_confidence)),
            ).fetchall()
        results: list[tuple[MemoryItem, float]] = []
        for row in rows:
            item = self._memory_item_from_row(row)
            if item.embedding is None or item.embedding.shape != query.shape:
                continue
            emb_norm = float(np.linalg.norm(item.embedding))
            if emb_norm == 0.0:
                continue
            score = float(np.dot(query_unit, item.embedding / emb_norm))
            if score < min_score:
                continue
            results.append((item, score + memory_item_supersession_boost(item)))
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results[:k]
    # ------------------------------------------------------------------
    # 분포 통계 (BIZ-29) — 임베딩 커버리지/클러스터 분포 모니터링용
    # ------------------------------------------------------------------

    def count_with_embedding(self) -> int:
        """임베딩 BLOB이 부착된 메시지 수를 반환한다.

        ``count()``와의 비율이 곧 시맨틱 메모리 커버리지이다.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
            ).fetchone()[0]

    def count_clustered(self) -> int:
        """``cluster_id``가 부착된 메시지 수를 반환한다."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE cluster_id IS NOT NULL"
            ).fetchone()[0]

    def count_unclustered_with_embedding(self) -> int:
        """임베딩은 있지만 아직 클러스터에 부착되지 않은 메시지 수.

        Phase 3 점진 클러스터링이 처리해야 할 backlog 크기이다.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE cluster_id IS NULL AND embedding IS NOT NULL"
            ).fetchone()[0]

    def embedding_dimension_distribution(self) -> dict[int, int]:
        """저장된 임베딩의 차원 분포를 ``{dim: count}`` 딕셔너리로 반환한다.

        설계 결정:
        - BLOB을 디코딩하지 않고 SQL ``length()`` + ``COUNT(*)``로 집계하여
          대용량 테이블에서도 빠르게 점검할 수 있게 한다.
        - float32(=4 bytes/dim) 가정. 다른 dtype이 섞이면 차원이 잘못 계산되므로
          이 결과 자체가 모델 교체/혼재 탐지 신호가 된다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT length(embedding), COUNT(*) FROM messages "
                "WHERE embedding IS NOT NULL "
                "GROUP BY length(embedding)"
            ).fetchall()
        distribution: dict[int, int] = {}
        for byte_len, cnt in rows:
            if byte_len is None:
                continue
            dim = int(byte_len) // 4  # float32 = 4 bytes
            distribution[dim] = distribution.get(dim, 0) + int(cnt)
        return distribution

    def cluster_member_counts(self) -> dict[int, int]:
        """클러스터별 실제 멤버 메시지 수를 ``{cluster_id: count}``로 반환한다.

        ``semantic_clusters.member_count``는 캐시 컬럼이라 갱신 누락이 가능하다.
        실측치와 비교하여 drift를 탐지하는 용도로 사용한다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT cluster_id, COUNT(*) FROM messages "
                "WHERE cluster_id IS NOT NULL "
                "GROUP BY cluster_id"
            ).fetchall()
        return {int(cid): int(cnt) for cid, cnt in rows}

    # ------------------------------------------------------------------
    # 시맨틱 메모리 (spec 005) — Phase 1: 저장소 API
    # ------------------------------------------------------------------

    def add_embedding(
        self,
        message_id: int,
        vector: Sequence[float] | np.ndarray,
    ) -> None:
        """주어진 메시지에 임베딩 벡터를 부착한다.

        벡터 차원은 호출자가 일관되게 관리한다(저장소는 강제하지 않음).
        동일 DB 내에서 차원이 섞여도 검색 시 query 차원과 다른 행은 자동 제외된다.

        Args:
            message_id: ``add_message()``가 반환한 행 id.
            vector: 임베딩 벡터(list/tuple/np.ndarray). float32로 변환되어 BLOB 저장된다.

        Raises:
            ValueError: ``message_id``가 존재하지 않거나, 벡터가 비어있는 경우.
        """
        arr = np.asarray(vector, dtype=_EMBEDDING_DTYPE)
        if arr.ndim != 1 or arr.size == 0:
            raise ValueError("embedding must be a non-empty 1-D vector")
        blob = arr.tobytes()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE messages SET embedding = ? WHERE id = ?",
                (blob, message_id),
            )
            if cursor.rowcount == 0:
                # 존재하지 않는 메시지에 임베딩을 붙이려는 시도는 호출자 버그
                raise ValueError(f"message_id {message_id} does not exist")

    def search_similar(
        self,
        query_vector: Sequence[float] | np.ndarray,
        k: int = 5,
        since: datetime | None = None,
    ) -> list[tuple[ConversationMessage, float]]:
        """코사인 유사도 상위 K개의 ``(메시지, 점수)`` 튜플을 반환한다.

        설계 결정:
        - Phase 1은 모든 후보 임베딩을 메모리로 끌어와 numpy로 코사인을 계산한다.
          메시지 수 ≤ 1만 가정 시 < 100ms. 그 이상은 Phase 2/3에서 sqlite-vec
          가상 테이블 도입을 검토한다.
        - query 차원과 다른 저장 행은 자동 제외(에러 없음). 차원 일관성은 호출자 책임.
        - embedding이 NULL인 행은 후보에서 제외한다(레거시 메시지 그레이스풀 처리).

        Args:
            query_vector: 검색 질의 벡터.
            k: 반환할 상위 결과 수.
            since: 주어지면 timestamp가 이 시점 이후(포함)인 메시지로 후보를 제한.

        Returns:
            유사도 내림차순 ``(ConversationMessage, similarity_score)`` 리스트.
            점수는 코사인 유사도 [-1.0, 1.0]. 후보 부족 시 K보다 적게 반환된다.

        Raises:
            ValueError: query_vector가 0 벡터(norm=0)이거나 비어있는 경우.
        """
        query = np.asarray(query_vector, dtype=_EMBEDDING_DTYPE)
        if query.ndim != 1 or query.size == 0:
            raise ValueError("query_vector must be a non-empty 1-D vector")
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0.0:
            # 0 벡터로는 코사인 유사도가 정의되지 않는다
            raise ValueError("query_vector must not be a zero vector")
        query_unit = query / query_norm
        query_dim = query.shape[0]

        sql = (
            "SELECT id, role, content, timestamp, token_count, embedding "
            "FROM messages WHERE embedding IS NOT NULL"
        )
        params: list = []
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since.isoformat())

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        results: list[tuple[ConversationMessage, float]] = []
        for _id, role, content, ts, tokens, blob in rows:
            try:
                emb = np.frombuffer(blob, dtype=_EMBEDDING_DTYPE)
            except (TypeError, ValueError):
                # BLOB 디코딩 실패 — 손상된 행은 조용히 건너뛴다
                continue
            if emb.shape[0] != query_dim:
                # 차원 불일치 행은 제외(에러 없이)
                continue
            emb_norm = float(np.linalg.norm(emb))
            if emb_norm == 0.0:
                continue
            score = float(np.dot(query_unit, emb / emb_norm))
            msg = ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            )
            results.append((msg, score))

        # 유사도 내림차순 정렬 후 상위 K개 반환
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:k]

    # ------------------------------------------------------------------
    # 시맨틱 클러스터 (spec 005 Phase 3) — 그래프형 드리밍 인덱스
    # ------------------------------------------------------------------

    def create_cluster(
        self,
        label: str,
        centroid: Sequence[float] | np.ndarray,
        summary: str = "",
        member_count: int = 0,
    ) -> int:
        """신규 시맨틱 클러스터를 생성하고 INSERT된 행 id를 반환한다.

        Args:
            label: 사람이 읽을 짧은 라벨 (LLM이 생성하기 전이면 빈 문자열 가능).
            centroid: 클러스터 평균 임베딩 벡터(float32로 변환).
            summary: 초기 요약 텍스트(보통 ``""``으로 시작, LLM이 채움).
            member_count: 멤버 메시지 수(보통 1로 시작).

        Returns:
            새로 생성된 ``semantic_clusters`` 행의 id.

        Raises:
            ValueError: centroid가 비어있거나 1-D가 아닌 경우.
        """
        arr = np.asarray(centroid, dtype=_EMBEDDING_DTYPE)
        if arr.ndim != 1 or arr.size == 0:
            raise ValueError("centroid must be a non-empty 1-D vector")
        now_iso = datetime.now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO semantic_clusters "
                "(label, centroid, summary, member_count, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (label, arr.tobytes(), summary, int(member_count), now_iso),
            )
            return int(cursor.lastrowid)

    def update_cluster(
        self,
        cluster_id: int,
        *,
        label: str | None = None,
        centroid: Sequence[float] | np.ndarray | None = None,
        summary: str | None = None,
        member_count: int | None = None,
    ) -> None:
        """클러스터의 일부 필드를 부분 갱신한다.

        ``None``이 아닌 인자만 SET 절에 포함되며, ``updated_at``은 항상 현재 시각으로 갱신된다.

        Args:
            cluster_id: 갱신할 클러스터 id.
            label: 새 라벨. None이면 변경하지 않음.
            centroid: 새 centroid 벡터. None이면 변경하지 않음.
            summary: 새 요약. None이면 변경하지 않음.
            member_count: 새 멤버 수. None이면 변경하지 않음.

        Raises:
            ValueError: 클러스터가 존재하지 않거나 centroid가 잘못된 경우.
        """
        sets: list[str] = []
        params: list = []
        if label is not None:
            sets.append("label = ?")
            params.append(label)
        if centroid is not None:
            arr = np.asarray(centroid, dtype=_EMBEDDING_DTYPE)
            if arr.ndim != 1 or arr.size == 0:
                raise ValueError("centroid must be a non-empty 1-D vector")
            sets.append("centroid = ?")
            params.append(arr.tobytes())
        if summary is not None:
            sets.append("summary = ?")
            params.append(summary)
        if member_count is not None:
            sets.append("member_count = ?")
            params.append(int(member_count))
        # updated_at은 어떤 변경이든 함께 갱신
        sets.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(cluster_id)

        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE semantic_clusters SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise ValueError(f"cluster_id {cluster_id} does not exist")

    def list_clusters(self) -> list[ClusterRecord]:
        """저장된 모든 클러스터를 id 오름차순으로 반환한다.

        클러스터링 알고리즘이 신규 메시지를 어느 클러스터에 부착할지 결정할 때 사용된다.
        centroid BLOB 디코딩 실패 행은 조용히 건너뛴다(데이터 손상 보호).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, label, centroid, summary, member_count, updated_at "
                "FROM semantic_clusters ORDER BY id"
            ).fetchall()

        records: list[ClusterRecord] = []
        for cid, label, blob, summary, member_count, updated_at in rows:
            try:
                centroid = np.frombuffer(blob, dtype=_EMBEDDING_DTYPE)
            except (TypeError, ValueError):
                logger.warning("Skipping cluster %d with corrupt centroid", cid)
                continue
            if centroid.size == 0:
                continue
            records.append(ClusterRecord(
                id=int(cid),
                label=label or "",
                centroid=centroid.copy(),  # frombuffer는 read-only view
                summary=summary or "",
                member_count=int(member_count),
                updated_at=datetime.fromisoformat(updated_at),
            ))
        return records

    def get_cluster(self, cluster_id: int) -> ClusterRecord | None:
        """단일 클러스터를 조회한다. 없으면 None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, label, centroid, summary, member_count, updated_at "
                "FROM semantic_clusters WHERE id = ?",
                (cluster_id,),
            ).fetchone()
        if row is None:
            return None
        cid, label, blob, summary, member_count, updated_at = row
        try:
            centroid = np.frombuffer(blob, dtype=_EMBEDDING_DTYPE)
        except (TypeError, ValueError):
            return None
        if centroid.size == 0:
            return None
        return ClusterRecord(
            id=int(cid),
            label=label or "",
            centroid=centroid.copy(),
            summary=summary or "",
            member_count=int(member_count),
            updated_at=datetime.fromisoformat(updated_at),
        )

    def assign_cluster(self, message_id: int, cluster_id: int | None) -> None:
        """메시지에 cluster_id를 부착(또는 해제)한다.

        ``cluster_id=None``이면 해당 메시지의 클러스터 멤버십을 제거한다.
        존재하지 않는 ``message_id``는 ValueError.
        ``cluster_id`` 자체의 존재 여부는 검증하지 않는다(외래 키 제약 없음, 호출자 책임).
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE messages SET cluster_id = ? WHERE id = ?",
                (cluster_id, message_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"message_id {message_id} does not exist")

    def get_messages_for_cluster(
        self, cluster_id: int
    ) -> list[ConversationMessage]:
        """특정 클러스터에 속한 메시지를 시간순으로 반환한다.

        클러스터별 LLM 요약 시 멤버 메시지를 모아 전달할 때 사용된다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count "
                "FROM messages WHERE cluster_id = ? ORDER BY id",
                (cluster_id,),
            ).fetchall()
        return [
            ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            )
            for role, content, ts, tokens in rows
        ]

    def get_unclustered_with_embeddings(
        self,
    ) -> list[tuple[int, ConversationMessage, np.ndarray]]:
        """``cluster_id IS NULL``이고 임베딩이 부착된 메시지를 시간순으로 반환한다.

        Phase 3 드리밍이 "아직 클러스터에 부착되지 않은 메시지"만 골라
        점진 클러스터링하기 위한 헬퍼이다. 임베딩이 NULL이거나 손상된 행은
        제외된다(클러스터링 불가).

        Returns:
            ``(message_id, ConversationMessage, embedding)`` 튜플 리스트.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, timestamp, token_count, embedding "
                "FROM messages "
                "WHERE cluster_id IS NULL AND embedding IS NOT NULL "
                "ORDER BY id"
            ).fetchall()

        results: list[tuple[int, ConversationMessage, np.ndarray]] = []
        for mid, role, content, ts, tokens, blob in rows:
            try:
                emb = np.frombuffer(blob, dtype=_EMBEDDING_DTYPE)
            except (TypeError, ValueError):
                continue
            if emb.size == 0:
                continue
            msg = ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            )
            results.append((int(mid), msg, emb.copy()))
        return results

    def get_message_with_embedding(
        self, message_id: int
    ) -> tuple[ConversationMessage, np.ndarray | None] | None:
        """메시지와 임베딩을 함께 조회한다.

        드리밍 파이프라인이 미처리 메시지의 임베딩을 일괄 조회할 때 사용한다.
        임베딩이 NULL이거나 손상되었으면 ``(message, None)`` 반환.
        메시지 자체가 없으면 ``None``.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT role, content, timestamp, token_count, embedding "
                "FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        role, content, ts, tokens, blob = row
        msg = ConversationMessage(
            role=MessageRole(role),
            content=content,
            timestamp=datetime.fromisoformat(ts),
            token_count=tokens,
        )
        if blob is None:
            return msg, None
        try:
            emb = np.frombuffer(blob, dtype=_EMBEDDING_DTYPE)
        except (TypeError, ValueError):
            return msg, None
        if emb.size == 0:
            return msg, None
        return msg, emb.copy()
