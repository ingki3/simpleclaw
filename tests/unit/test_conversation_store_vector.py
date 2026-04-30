"""ConversationStore 시맨틱 메모리(임베딩) 확장에 대한 단위 테스트.

검증 범위 (spec 005 Phase 1):
- 임베딩 저장 후 search_similar 라운드트립
- 차원 불일치/NULL 임베딩 행 자동 제외
- since 시간 필터 결합
- 0 벡터 / 빈 벡터 / 존재하지 않는 message_id 시 ValueError
- 빈 DB / 임베딩 0개 DB에 대한 search_similar
- 레거시 DB(embedding 컬럼 없음) 자동 마이그레이션
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "test.db")


def _msg(content: str, role: MessageRole = MessageRole.USER) -> ConversationMessage:
    return ConversationMessage(role=role, content=content)


class TestAddMessageReturnsId:
    def test_add_message_returns_increasing_ids(self, store):
        id1 = store.add_message(_msg("first"))
        id2 = store.add_message(_msg("second"))
        assert isinstance(id1, int)
        assert id2 == id1 + 1


class TestAddEmbedding:
    def test_attach_then_search_roundtrip(self, store):
        mid = store.add_message(_msg("apple"))
        store.add_embedding(mid, [1.0, 0.0, 0.0])
        results = store.search_similar([1.0, 0.0, 0.0], k=5)
        assert len(results) == 1
        msg, score = results[0]
        assert msg.content == "apple"
        assert score == pytest.approx(1.0, abs=1e-5)

    def test_accepts_numpy_array(self, store):
        mid = store.add_message(_msg("banana"))
        store.add_embedding(mid, np.array([0.5, 0.5, 0.5], dtype=np.float32))
        results = store.search_similar(np.array([0.5, 0.5, 0.5]), k=1)
        assert len(results) == 1

    def test_invalid_message_id_raises(self, store):
        with pytest.raises(ValueError, match="does not exist"):
            store.add_embedding(9999, [1.0, 0.0])

    def test_empty_vector_raises(self, store):
        mid = store.add_message(_msg("x"))
        with pytest.raises(ValueError, match="non-empty"):
            store.add_embedding(mid, [])

    def test_multidim_vector_raises(self, store):
        mid = store.add_message(_msg("x"))
        with pytest.raises(ValueError, match="1-D"):
            store.add_embedding(mid, np.zeros((2, 2)))

    def test_overwrite_embedding(self, store):
        mid = store.add_message(_msg("x"))
        store.add_embedding(mid, [1.0, 0.0])
        store.add_embedding(mid, [0.0, 1.0])
        # 마지막 벡터로 검색 시 점수 1.0
        results = store.search_similar([0.0, 1.0], k=1)
        assert results[0][1] == pytest.approx(1.0, abs=1e-5)


class TestSearchSimilar:
    def test_orders_by_cosine_similarity(self, store):
        # 세 개의 벡터: query와 (정합, 직교, 반대)
        ids = [
            store.add_message(_msg("aligned")),
            store.add_message(_msg("orthogonal")),
            store.add_message(_msg("opposite")),
        ]
        store.add_embedding(ids[0], [1.0, 0.0])
        store.add_embedding(ids[1], [0.0, 1.0])
        store.add_embedding(ids[2], [-1.0, 0.0])

        results = store.search_similar([1.0, 0.0], k=3)
        contents = [m.content for m, _ in results]
        scores = [s for _, s in results]
        assert contents == ["aligned", "orthogonal", "opposite"]
        assert scores[0] == pytest.approx(1.0, abs=1e-5)
        assert scores[1] == pytest.approx(0.0, abs=1e-5)
        assert scores[2] == pytest.approx(-1.0, abs=1e-5)

    def test_k_caps_results(self, store):
        for i in range(5):
            mid = store.add_message(_msg(f"m{i}"))
            store.add_embedding(mid, [float(i + 1), 0.0])
        assert len(store.search_similar([1.0, 0.0], k=2)) == 2
        assert len(store.search_similar([1.0, 0.0], k=10)) == 5

    def test_excludes_messages_without_embedding(self, store):
        # 임베딩 없는 메시지는 후보에서 제외
        store.add_message(_msg("no embedding"))
        mid = store.add_message(_msg("with embedding"))
        store.add_embedding(mid, [1.0, 0.0])

        results = store.search_similar([1.0, 0.0], k=10)
        assert len(results) == 1
        assert results[0][0].content == "with embedding"

    def test_excludes_dimension_mismatch(self, store):
        # 차원이 다른 행은 자동 제외(에러 없음)
        m_ok = store.add_message(_msg("3d"))
        m_bad = store.add_message(_msg("2d"))
        store.add_embedding(m_ok, [1.0, 0.0, 0.0])
        store.add_embedding(m_bad, [1.0, 0.0])

        results = store.search_similar([1.0, 0.0, 0.0], k=10)
        assert len(results) == 1
        assert results[0][0].content == "3d"

    def test_since_filter(self, store):
        old = datetime.now() - timedelta(hours=2)
        m_old = store.add_message(ConversationMessage(
            role=MessageRole.USER, content="old", timestamp=old,
        ))
        m_new = store.add_message(_msg("new"))
        store.add_embedding(m_old, [1.0, 0.0])
        store.add_embedding(m_new, [1.0, 0.0])

        results = store.search_similar(
            [1.0, 0.0], k=10, since=datetime.now() - timedelta(minutes=5),
        )
        contents = {m.content for m, _ in results}
        assert contents == {"new"}

    def test_zero_query_raises(self, store):
        with pytest.raises(ValueError, match="zero vector"):
            store.search_similar([0.0, 0.0], k=1)

    def test_empty_query_raises(self, store):
        with pytest.raises(ValueError, match="non-empty"):
            store.search_similar([], k=1)

    def test_empty_db_returns_empty(self, store):
        assert store.search_similar([1.0, 0.0], k=5) == []

    def test_db_with_no_embeddings_returns_empty(self, store):
        store.add_message(_msg("a"))
        store.add_message(_msg("b"))
        assert store.search_similar([1.0, 0.0], k=5) == []

    def test_zero_norm_stored_vector_excluded(self, store):
        # 저장된 벡터가 0 벡터인 경우 점수가 정의 불가 → 제외
        m_zero = store.add_message(_msg("zero"))
        m_ok = store.add_message(_msg("ok"))
        store.add_embedding(m_zero, [0.0, 0.0])
        store.add_embedding(m_ok, [1.0, 0.0])

        results = store.search_similar([1.0, 0.0], k=10)
        assert len(results) == 1
        assert results[0][0].content == "ok"


class TestLegacyMigration:
    def test_migrates_legacy_db_without_embedding_column(self, tmp_path):
        # 005 도입 이전 스키마(embedding 컬럼 없음)로 DB를 직접 생성
        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "INSERT INTO messages (role, content, timestamp, token_count) "
                "VALUES (?, ?, ?, ?)",
                ("user", "legacy row", datetime.now().isoformat(), 0),
            )

        # ConversationStore 인스턴스화 시 embedding 컬럼이 자동 추가되어야 한다
        store = ConversationStore(db_path)
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "embedding" in cols

        # 기존 데이터는 보존되며 embedding 없이도 검색 시 NULL로 자동 제외된다
        assert store.count() == 1
        new_id = store.add_message(_msg("new"))
        store.add_embedding(new_id, [1.0, 0.0])
        results = store.search_similar([1.0, 0.0], k=10)
        assert len(results) == 1
        assert results[0][0].content == "new"

    def test_wal_mode_enabled(self, tmp_path):
        ConversationStore(tmp_path / "wal.db")
        with sqlite3.connect(tmp_path / "wal.db") as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
