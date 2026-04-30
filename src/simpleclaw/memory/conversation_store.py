"""대화 이력을 SQLite 데이터베이스에 저장하고 조회하는 모듈.

주요 동작 흐름:
1. ConversationStore 인스턴스 생성 시 SQLite DB 파일 경로를 받아 스키마를 자동 생성·마이그레이션한다.
2. add_message()로 대화 메시지를 순차 저장하며, INSERT된 행 id를 반환한다.
3. get_recent() / get_since()로 최근 또는 특정 시점 이후 대화를 시간순으로 조회한다.
4. add_embedding() / search_similar()로 메시지 단위 임베딩을 저장·검색한다(시맨틱 메모리, spec 005).

설계 결정:
- 각 메서드 호출마다 sqlite3.connect()를 사용하여 연결을 열고 닫는다.
  장기 실행 프로세스에서 파일 잠금 문제를 방지하기 위함이다.
- 메시지 순서는 auto-increment id 기준이며, timestamp는 보조 필터로만 사용한다.
- 임베딩은 float32 little-endian 연속 BLOB로 저장한다(numpy.tobytes 직렬화).
  Phase 1에서는 코사인 유사도를 인메모리(numpy)로 계산한다. 메시지 수가 1만 이상으로
  커질 경우 sqlite-vec 가상 테이블 도입을 Phase 2/3에서 검토한다.
- 저널 모드는 WAL로 설정하여 데몬·드리밍 동시 쓰기 시 잠금 충돌을 줄인다.
- 기존 행에 embedding 컬럼이 없을 때 자동으로 ALTER TABLE 마이그레이션한다(멱등).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import numpy as np

from simpleclaw.memory.models import ConversationMessage, MessageRole

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

    def _ensure_schema(self) -> None:
        """messages 테이블 생성·마이그레이션 + WAL 모드 설정.

        설계 결정:
        - 신규 DB는 처음부터 embedding 컬럼을 포함해 생성한다.
        - 기존 DB(005 도입 이전)에는 ALTER TABLE로 컬럼만 추가한다(데이터 보존).
        - WAL 저널 모드는 영구 적용이며 멱등하므로 매 초기화마다 호출해도 무방하다.
        """
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            # WAL: reader-writer 동시성 확보(데몬 + 드리밍 동시 쓰기 시 잠금 충돌 완화)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    embedding BLOB
                )
            """)
            # 기존 DB 마이그레이션: embedding 컬럼이 없으면 추가
            cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
            if "embedding" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN embedding BLOB")
                logger.info("Migrated messages table: added embedding column")

    def add_message(self, message: ConversationMessage) -> int:
        """새 대화 메시지를 DB에 저장하고 INSERT된 행 id를 반환한다.

        반환 id는 이후 ``add_embedding()`` 호출 시 message_id로 사용한다.
        기존 호출자는 반환값을 무시해도 무방하다(이전 시그니처 호환).

        Args:
            message: 저장할 대화 메시지 객체.

        Returns:
            INSERT된 messages 행의 id.
        """
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO messages (role, content, timestamp, token_count) "
                "VALUES (?, ?, ?, ?)",
                (
                    message.role.value,
                    message.content,
                    message.timestamp.isoformat(),
                    message.token_count,
                ),
            )
            return int(cursor.lastrowid)

    def get_recent(self, limit: int = 20) -> list[ConversationMessage]:
        """최근 메시지를 시간순으로 반환한다.

        Args:
            limit: 가져올 최대 메시지 수. 기본값 20.

        Returns:
            시간순(오래된 것 먼저)으로 정렬된 ConversationMessage 리스트.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count FROM messages "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        messages = []
        # DB에서 역순(최신 먼저)으로 가져온 뒤 다시 뒤집어 시간순으로 만든다
        for role, content, ts, tokens in reversed(rows):
            messages.append(ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            ))
        return messages

    def get_since(self, since: datetime) -> list[ConversationMessage]:
        """지정된 시점 이후의 메시지를 시간순으로 반환한다.

        Args:
            since: 이 시점 이후(포함)의 메시지를 조회한다.

        Returns:
            시간순으로 정렬된 ConversationMessage 리스트.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count FROM messages "
                "WHERE timestamp >= ? ORDER BY id",
                (since.isoformat(),),
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

    def count(self) -> int:
        """저장된 전체 메시지 수를 반환한다."""
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

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
        with sqlite3.connect(self._db_path) as conn:
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

        with sqlite3.connect(self._db_path) as conn:
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
