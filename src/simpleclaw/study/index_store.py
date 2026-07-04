"""Study Wiki 의 구조화 retrieval index (SQLite).

Markdown 위키(:mod:`~simpleclaw.study.markdown`)가 사람이 읽는 source of truth 이지만,
질문이 들어왔을 때 빠른 retrieval 과 freshness filtering 에는 구조화 index 가 필요하다.
이 모듈의 :class:`StudyIndexStore` 는 ``study_topics`` / ``study_items`` 테이블을 감싸
upsert · lexical search · freshness query 를 제공한다.

설계 결정:
- **스키마 SoT 단일화**: 테이블 정의는 conversations DB migration
  ``0005_agent_study_items.sql`` 한 곳에만 둔다. 프로덕션에서는 부팅 시 migration
  runner 가 conversations.db 에 적용하고, 독립 index DB(``index.sqlite``)에서는
  :meth:`initialize_schema` 가 *동일한* SQL 파일을 읽어 idempotent 하게 실행한다.
  ``CREATE ... IF NOT EXISTS`` 이므로 두 경로가 충돌하지 않고, 스키마가 한 곳에서만
  진화한다.
- **사용자 메모리와 분리**: 본 store 는 ``memory_items`` 를 절대 건드리지 않는다.
  외부 세계 배경지식과 사용자 자신에 대한 기억의 경계가 이 기능의 존재 이유다
  (docs/agent-study-wiki.md §2). 운영자 조회(``StudyWikiStore``, BIZ-395)도 같은
  ``index.sqlite`` 의 ``study_items`` 를 read-only 로 읽는다.
- **lexical 우선**: ``embedding`` 컬럼은 후속 단계의 semantic search 를 위해 보존하되,
  본 단계는 외부 의존성 없는 LIKE 기반 lexical search 만 구현한다.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import simpleclaw.db as _db_pkg
from simpleclaw.study.types import StudyItemRecord, StudyItemStatus

logger = logging.getLogger(__name__)

# 스키마 SoT — conversations migration 파일을 그대로 재사용한다(상단 docstring 참고).
_SCHEMA_SQL_PATH = (
    Path(_db_pkg.__file__).resolve().parent
    / "migrations_data"
    / "conversations"
    / "0005_agent_study_items.sql"
)

# LIKE 패턴에서 와일드카드로 오인되는 문자를 이스케이프하기 위한 escape 문자.
_LIKE_ESCAPE = "\\"

# lexical query 를 단어 단위로 쪼개는 정규식. 영문/숫자/한글을 한 토큰으로 본다.
_TERM_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _now_iso() -> str:
    """현재 시각을 로컬 타임존 ISO8601 로 반환한다.

    테스트는 명시 타임스탬프를 주입하므로, 이 helper 는 호출자가 시각을 생략했을
    때의 기본값으로만 쓰인다.
    """
    return datetime.now().astimezone().isoformat()


def _escape_like(term: str) -> str:
    """LIKE 패턴 내 ``%`` / ``_`` / escape 문자를 리터럴로 만든다."""
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def _parse_ts(value: str | None) -> datetime | None:
    """ISO8601 문자열을 datetime 으로 파싱한다. 실패/None 이면 None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.debug("study index: 파싱 불가한 타임스탬프 무시: %r", value)
        return None


def _ts_lte(a: datetime, b: datetime) -> bool:
    """``a <= b`` 를 tz-aware/naive 혼재에 안전하게 비교한다.

    한쪽만 aware 면 비교가 ``TypeError`` 를 내므로, naive 를 로컬 타임존으로 간주해
    aware 로 승격한 뒤 비교한다.
    """
    if (a.tzinfo is None) != (b.tzinfo is None):
        if a.tzinfo is None:
            a = a.astimezone()
        if b.tzinfo is None:
            b = b.astimezone()
    return a <= b


class StudyIndexStore:
    """``study_topics`` / ``study_items`` 를 감싸는 retrieval index store.

    스레드 안전하지 않다. 매 작업마다 새 연결을 열고 닫는다(일일 study runner 는
    단일 사이클만 실행되므로 connection 재사용 이득보다 격리가 중요).
    """

    def __init__(self, path: str | Path) -> None:
        """대상 SQLite 경로로 store 를 초기화한다.

        Args:
            path: index DB 경로. 운영 배치에서는 위키 루트의 ``index.sqlite``
                (:func:`simpleclaw.study.paths.index_path`)를 가리킨다. 부모
                디렉터리가 없으면 첫 연결 시 생성한다. ``":memory:"`` 도 가능하나
                연결을 재사용하지 않으므로 의미가 제한적이다.
        """
        self._path = str(path)
        self._is_memory = self._path == ":memory:"
        if not self._is_memory:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> str:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        """FK 제약을 켠 연결을 연다. 호출자가 close 를 책임진다."""
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ------------------------------------------------------------------
    # 스키마
    # ------------------------------------------------------------------

    def initialize_schema(self) -> None:
        """migration SoT SQL 을 읽어 idempotent 하게 테이블을 생성한다.

        프로덕션 conversations.db 는 이미 migration runner 가 같은 SQL 을 적용했으므로
        ``IF NOT EXISTS`` 로 무해하게 no-op 이 된다. 독립 ``index.sqlite`` 경로에서는
        이 호출이 유일한 스키마 생성 지점이다.
        """
        sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        conn = self._connect()
        try:
            with conn:
                conn.executescript(sql)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 쓰기 API
    # ------------------------------------------------------------------

    def upsert_topic(
        self,
        *,
        id: str,
        label: str,
        description: str = "",
        status: str = "active",
        priority: str = "medium",
        tags: Sequence[str] | None = None,
        source: str = "manual",
        interest_score: float = 0.0,
        importance_score: float = 0.0,
        metadata: Mapping[str, object] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        """topic 을 삽입하거나 갱신한다(PK ``id`` 충돌 시 update).

        ``created_at`` 은 최초 삽입 시각을 보존하고(충돌 시 기존 값 유지), ``updated_at``
        은 매 호출마다 갱신한다. 시각을 생략하면 현재 시각을 쓴다.
        """
        now = _now_iso()
        created = created_at or now
        updated = updated_at or now
        tags_json = json.dumps(list(tags or []), ensure_ascii=False)
        metadata_json = json.dumps(dict(metadata or {}), ensure_ascii=False)

        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO study_topics (
                        id, label, description, status, priority, tags_json,
                        source, interest_score, importance_score,
                        created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        label = excluded.label,
                        description = excluded.description,
                        status = excluded.status,
                        priority = excluded.priority,
                        tags_json = excluded.tags_json,
                        source = excluded.source,
                        interest_score = excluded.interest_score,
                        importance_score = excluded.importance_score,
                        updated_at = excluded.updated_at,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        id, label, description, status, priority, tags_json,
                        source, float(interest_score), float(importance_score),
                        created, updated, metadata_json,
                    ),
                )
        finally:
            conn.close()

    def add_item(
        self,
        *,
        topic_id: str,
        title: str,
        text: str,
        retrieved_at: str,
        source_url: str = "",
        source_title: str = "",
        status: str = StudyItemStatus.UNKNOWN.value,
        confidence: float = 0.0,
        importance: float = 0.0,
        published_at: str | None = None,
        valid_until: str | None = None,
        embedding: bytes | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """study item 한 건을 추가하고 새 행 id 를 반환한다.

        Args:
            topic_id: 소속 topic id. ``study_topics`` 에 존재해야 한다(FK).
            retrieved_at: 수집 시각(ISO8601). freshness 계산 기준이라 필수.

        Returns:
            새로 생성된 ``study_items.id`` (1 이상).
        """
        metadata_json = json.dumps(dict(metadata or {}), ensure_ascii=False)
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    """
                    INSERT INTO study_items (
                        topic_id, title, text, source_url, source_title,
                        status, confidence, importance, published_at,
                        retrieved_at, valid_until, embedding, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        topic_id, title, text, source_url, source_title,
                        status, float(confidence), float(importance), published_at,
                        retrieved_at, valid_until, embedding, metadata_json,
                    ),
                )
                return int(cur.lastrowid)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 읽기 API
    # ------------------------------------------------------------------

    def get_item(self, item_id: int) -> StudyItemRecord | None:
        """id 로 item 한 건을 조회한다(없으면 None)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM study_items WHERE id = ?", (item_id,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_record(row) if row is not None else None

    def list_items(
        self, topic_id: str, *, limit: int = 50
    ) -> list[StudyItemRecord]:
        """한 topic 의 item 을 최신 수집순으로 반환한다."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM study_items
                WHERE topic_id = ?
                ORDER BY retrieved_at DESC, id DESC
                LIMIT ?
                """,
                (topic_id, int(limit)),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_record(r) for r in rows]

    def search_lexical(
        self,
        query: str,
        *,
        limit: int = 10,
        topic_id: str | None = None,
    ) -> list[StudyItemRecord]:
        """title/text 에 대한 LIKE 기반 lexical search.

        query 를 단어 토큰으로 쪼개, title+text 에 등장하는 토큰 수가 많은 순으로
        정렬한다(동점 시 importance · confidence · 최신 수집순). 토큰이 하나도 없거나
        매칭 0 이면 빈 리스트.

        Args:
            query: 검색어. 공백/구두점으로 토큰화된다.
            limit: 최대 반환 건수.
            topic_id: 지정 시 해당 topic 으로 검색 범위를 한정한다.
        """
        terms = _TERM_RE.findall(query.lower())
        if not terms:
            return []

        # 토큰별 LIKE 매칭 합으로 relevance 점수를 만든다. haystack 은 title+text 소문자.
        haystack = "(lower(title) || ' ' || lower(text))"
        score_expr = " + ".join(
            f"(CASE WHEN {haystack} LIKE ? ESCAPE ? THEN 1 ELSE 0 END)"
            for _ in terms
        )

        def _like_params() -> list[object]:
            out: list[object] = []
            for term in terms:
                out.append(f"%{_escape_like(term)}%")
                out.append(_LIKE_ESCAPE)
            return out

        where = f"({score_expr}) > 0"
        tail_params: list[object] = []
        if topic_id is not None:
            where += " AND topic_id = ?"
            tail_params.append(topic_id)
        tail_params.append(int(limit))

        sql = f"""
            SELECT *, ({score_expr}) AS _score
            FROM study_items
            WHERE {where}
            ORDER BY _score DESC, importance DESC, confidence DESC,
                     retrieved_at DESC, id DESC
            LIMIT ?
        """
        # score_expr 가 SELECT 와 WHERE 두 곳에 등장하므로 LIKE 파라미터를 두 번 바인딩.
        params = [*_like_params(), *_like_params(), *tail_params]

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [_row_to_record(r) for r in rows]

    def fresh_items(
        self,
        *,
        within_hours: float,
        now: str | datetime | None = None,
        topic_id: str | None = None,
        min_confidence: float = 0.0,
        exclude_statuses: Sequence[str] = (StudyItemStatus.STALE.value,),
        limit: int = 50,
    ) -> list[StudyItemRecord]:
        """freshness/신뢰도 gate 를 통과한 item 만 최신순으로 반환한다.

        한 item 이 "신선" 하려면:
        1. ``retrieved_at`` 이 ``now - within_hours`` 이후이고,
        2. ``valid_until`` 이 있으면 ``now`` 가 그 이전이며,
        3. ``confidence >= min_confidence`` 이고,
        4. status 가 ``exclude_statuses`` 에 없어야 한다(기본: STALE 제외).

        타임존이 섞일 수 있어(예: naive vs +09:00) 시각 비교는 Python 에서 파싱 후
        수행한다. ``retrieved_at`` 인덱스로 1차 정렬만 DB 에 맡긴다.

        Args:
            within_hours: 신선도 허용 창(시간).
            now: 기준 시각. 생략 시 현재 시각. ISO 문자열 또는 ``datetime``.
        """
        if isinstance(now, datetime):
            now_dt = now
        else:
            now_dt = _parse_ts(now) or datetime.now().astimezone()
        cutoff = now_dt - timedelta(hours=float(within_hours))
        excluded = set(exclude_statuses)

        clauses = ["confidence >= ?"]
        params: list[object] = [float(min_confidence)]
        if topic_id is not None:
            clauses.append("topic_id = ?")
            params.append(topic_id)

        sql = f"""
            SELECT * FROM study_items
            WHERE {" AND ".join(clauses)}
            ORDER BY retrieved_at DESC, id DESC
        """
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        out: list[StudyItemRecord] = []
        for row in rows:
            if row["status"] in excluded:
                continue
            retrieved = _parse_ts(row["retrieved_at"])
            # 파싱 불가하거나 창 밖이면 제외. 신선하다고 잘못 포함하느니 빠뜨리는 게
            # 안전하다(낡은 외부 사실 영속 방어).
            if retrieved is None or not _ts_lte(cutoff, retrieved):
                continue
            valid_until = _parse_ts(row["valid_until"])
            if valid_until is not None and not _ts_lte(now_dt, valid_until):
                continue
            out.append(_row_to_record(row))
            if len(out) >= limit:
                break
        return out


def _row_to_record(row: sqlite3.Row) -> StudyItemRecord:
    """``study_items`` 행을 :class:`StudyItemRecord` 로 변환한다."""
    try:
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        if not isinstance(metadata, dict):
            metadata = {}
    except json.JSONDecodeError:
        metadata = {}
    return StudyItemRecord(
        id=int(row["id"]),
        topic_id=row["topic_id"],
        title=row["title"],
        text=row["text"],
        source_url=row["source_url"],
        source_title=row["source_title"],
        status=row["status"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        published_at=row["published_at"],
        retrieved_at=row["retrieved_at"],
        valid_until=row["valid_until"],
        metadata=metadata,
        embedding=row["embedding"],
    )
