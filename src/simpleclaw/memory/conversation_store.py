"""대화 이력을 SQLite 데이터베이스에 저장하고 조회하는 모듈.

주요 동작 흐름:
1. ConversationStore 인스턴스 생성 시 SQLite DB 파일 경로를 받아 스키마를 자동 생성·마이그레이션한다.
2. add_message()로 대화 메시지를 순차 저장하며, INSERT된 행 id를 반환한다.
3. get_recent() / get_since()로 최근 또는 특정 시점 이후 대화를 시간순으로 조회한다.
4. add_embedding() / search_similar()로 메시지 단위 임베딩을 저장·검색한다(시맨틱 메모리, spec 005).
5. create_cluster() / list_clusters() / assign_cluster() 등으로 시맨틱 클러스터 그래프를 관리한다(Phase 3).

설계 결정:
- 각 메서드 호출마다 sqlite3.connect()를 사용하여 연결을 열고 닫는다.
  장기 실행 프로세스에서 파일 잠금 문제를 방지하기 위함이다.
- 메시지 순서는 auto-increment id 기준이며, timestamp는 보조 필터로만 사용한다.
- 임베딩은 float32 little-endian 연속 BLOB로 저장한다(numpy.tobytes 직렬화).
  Phase 1에서는 코사인 유사도를 인메모리(numpy)로 계산한다. 메시지 수가 1만 이상으로
  커질 경우 sqlite-vec 가상 테이블 도입을 Phase 2/3에서 검토한다.
- 저널 모드는 WAL로 설정하여 데몬·드리밍 동시 쓰기 시 잠금 충돌을 줄인다.
- 기존 행에 embedding 컬럼이 없을 때 자동으로 ALTER TABLE 마이그레이션한다(멱등).
- Phase 3에서 ``semantic_clusters`` 테이블을 추가하고 ``messages.cluster_id`` 컬럼으로
  메시지를 클러스터에 부착한다. cluster_id는 외래 키 제약 없이 정수 참조로만 둔다
  (클러스터 삭제 시 멤버 메시지는 보존하되 cluster_id가 dangling 상태로 남는다 — 무시).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import numpy as np

from simpleclaw.db import run_conversations_migrations
from simpleclaw.memory.models import ClusterRecord, ConversationMessage, MessageRole

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
        with sqlite3.connect(self._db_path) as conn:
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
            # BIZ-77 — channel 컬럼은 마이그레이션 0002 가 추가한다.
            # 여기서 보충하면 0002 의 ALTER 가 duplicate 로 실패하므로, 정규화는
            # baseline (embedding/cluster_id) 까지만 처리하고 이후 컬럼은 마이그레이션
            # 러너에 위임한다.

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
        with sqlite3.connect(self._db_path) as conn:
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

    def get_recent(self, limit: int = 20) -> list[ConversationMessage]:
        """최근 메시지를 시간순으로 반환한다.

        Args:
            limit: 가져올 최대 메시지 수. 기본값 20.

        Returns:
            시간순(오래된 것 먼저)으로 정렬된 ConversationMessage 리스트.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count, channel FROM messages "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        messages = []
        # DB에서 역순(최신 먼저)으로 가져온 뒤 다시 뒤집어 시간순으로 만든다
        for role, content, ts, tokens, channel in reversed(rows):
            messages.append(ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
                channel=channel,
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
                "SELECT role, content, timestamp, token_count, channel FROM messages "
                "WHERE timestamp >= ? ORDER BY id",
                (since.isoformat(),),
            ).fetchall()

        return [
            ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
                channel=channel,
            )
            for role, content, ts, tokens, channel in rows
        ]

    # ------------------------------------------------------------------
    # ID-bearing fetch (BIZ-77) — 인사이트 source 역추적을 위한 헬퍼
    # ------------------------------------------------------------------

    def get_recent_with_ids(
        self, limit: int = 20
    ) -> list[tuple[int, ConversationMessage]]:
        """최근 메시지를 ``(id, message)`` 쌍으로 시간순 반환.

        ``get_recent`` 와 동일하지만 message rowid 를 함께 노출한다 — 드리밍이
        인사이트 메타에 ``source_msg_ids`` 로 적재해야 하므로 BIZ-77 에서 추가됨.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, role, content, timestamp, token_count, channel "
                "FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results: list[tuple[int, ConversationMessage]] = []
        # DB에서 역순(최신 먼저)으로 가져온 뒤 다시 뒤집어 시간순으로 만든다.
        for mid, role, content, ts, tokens, channel in reversed(rows):
            msg = ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
                channel=channel,
            )
            results.append((int(mid), msg))
        return results

    def get_since_with_ids(
        self, since: datetime
    ) -> list[tuple[int, ConversationMessage]]:
        """지정 시점 이후 메시지를 ``(id, message)`` 쌍으로 시간순 반환."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, role, content, timestamp, token_count, channel "
                "FROM messages WHERE timestamp >= ? ORDER BY id",
                (since.isoformat(),),
            ).fetchall()

        return [
            (
                int(mid),
                ConversationMessage(
                    role=MessageRole(role),
                    content=content,
                    timestamp=datetime.fromisoformat(ts),
                    token_count=tokens,
                    channel=channel,
                ),
            )
            for mid, role, content, ts, tokens, channel in rows
        ]

    def get_messages_by_ids(
        self, ids: list[int]
    ) -> list[tuple[int, ConversationMessage]]:
        """주어진 rowid 들을 시간순으로 조회한다.

        존재하지 않는 id 는 결과에서 그냥 빠진다(에러 없음). Admin API 가
        인사이트의 ``source_msg_ids`` 를 사람이 읽을 메시지로 풀어내기 위해 사용.

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
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT id, role, content, timestamp, token_count, channel "
                f"FROM messages WHERE id IN ({placeholders}) ORDER BY id",
                unique_ids,
            ).fetchall()

        return [
            (
                int(mid),
                ConversationMessage(
                    role=MessageRole(role),
                    content=content,
                    timestamp=datetime.fromisoformat(ts),
                    token_count=tokens,
                    channel=channel,
                ),
            )
            for mid, role, content, ts, tokens, channel in rows
        ]

    def count(self) -> int:
        """저장된 전체 메시지 수를 반환한다."""
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    # ------------------------------------------------------------------
    # 분포 통계 (BIZ-29) — 임베딩 커버리지/클러스터 분포 모니터링용
    # ------------------------------------------------------------------

    def count_with_embedding(self) -> int:
        """임베딩 BLOB이 부착된 메시지 수를 반환한다.

        ``count()``와의 비율이 곧 시맨틱 메모리 커버리지이다.
        """
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
            ).fetchone()[0]

    def count_clustered(self) -> int:
        """``cluster_id``가 부착된 메시지 수를 반환한다."""
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE cluster_id IS NOT NULL"
            ).fetchone()[0]

    def count_unclustered_with_embedding(self) -> int:
        """임베딩은 있지만 아직 클러스터에 부착되지 않은 메시지 수.

        Phase 3 점진 클러스터링이 처리해야 할 backlog 크기이다.
        """
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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

        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
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
