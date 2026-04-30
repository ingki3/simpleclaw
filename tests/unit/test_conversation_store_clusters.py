"""ConversationStore 시맨틱 클러스터(Phase 3) CRUD 단위 테스트.

검증 범위:
- semantic_clusters 테이블 자동 생성, messages.cluster_id 컬럼 마이그레이션
- create_cluster / list_clusters / get_cluster / update_cluster 라운드트립
- assign_cluster — 메시지에 클러스터 부착·해제
- get_messages_for_cluster — 멤버 메시지 시간순 조회
- get_unclustered_with_embeddings — 임베딩 부착되었으나 cluster_id가 NULL인 메시지만 반환
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "test.db")


def _msg(content: str, role: MessageRole = MessageRole.USER) -> ConversationMessage:
    return ConversationMessage(role=role, content=content)


class TestSchema:
    def test_semantic_clusters_table_created(self, store, tmp_path):
        with sqlite3.connect(store._db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "semantic_clusters" in tables

    def test_messages_has_cluster_id(self, store):
        with sqlite3.connect(store._db_path) as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
        assert "cluster_id" in cols

    def test_legacy_db_gets_cluster_id_migration(self, tmp_path):
        # cluster_id가 없는 레거시 messages 테이블 직접 생성
        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    embedding BLOB
                )
            """)
        ConversationStore(db_path)
        with sqlite3.connect(db_path) as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
        assert "cluster_id" in cols


class TestCreateCluster:
    def test_create_returns_id(self, store):
        cid = store.create_cluster(
            label="weather", centroid=[1.0, 0.0], summary="sunny days", member_count=1
        )
        assert isinstance(cid, int)
        assert cid > 0

    def test_create_with_numpy(self, store):
        cid = store.create_cluster(
            label="x", centroid=np.array([0.5, 0.5], dtype=np.float32)
        )
        cluster = store.get_cluster(cid)
        assert cluster is not None
        np.testing.assert_allclose(cluster.centroid, [0.5, 0.5])

    def test_empty_centroid_raises(self, store):
        with pytest.raises(ValueError, match="non-empty"):
            store.create_cluster(label="x", centroid=[])

    def test_multidim_centroid_raises(self, store):
        with pytest.raises(ValueError, match="1-D"):
            store.create_cluster(label="x", centroid=np.zeros((2, 2)))


class TestListClusters:
    def test_empty_returns_empty(self, store):
        assert store.list_clusters() == []

    def test_orders_by_id(self, store):
        c1 = store.create_cluster(label="a", centroid=[1.0, 0.0])
        c2 = store.create_cluster(label="b", centroid=[0.0, 1.0])
        records = store.list_clusters()
        assert [r.id for r in records] == [c1, c2]
        assert [r.label for r in records] == ["a", "b"]

    def test_centroid_roundtrip(self, store):
        store.create_cluster(label="a", centroid=[1.5, 2.5, 3.5])
        records = store.list_clusters()
        np.testing.assert_allclose(records[0].centroid, [1.5, 2.5, 3.5])
        assert records[0].centroid.dtype == np.float32


class TestGetCluster:
    def test_returns_record(self, store):
        cid = store.create_cluster(
            label="x", centroid=[1.0, 0.0], summary="s", member_count=3
        )
        cluster = store.get_cluster(cid)
        assert cluster is not None
        assert cluster.id == cid
        assert cluster.label == "x"
        assert cluster.summary == "s"
        assert cluster.member_count == 3

    def test_missing_returns_none(self, store):
        assert store.get_cluster(9999) is None


class TestUpdateCluster:
    def test_partial_update_label(self, store):
        cid = store.create_cluster(label="old", centroid=[1.0, 0.0])
        store.update_cluster(cid, label="new")
        cluster = store.get_cluster(cid)
        assert cluster.label == "new"

    def test_update_centroid(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0, 0.0])
        store.update_cluster(cid, centroid=[0.0, 1.0])
        cluster = store.get_cluster(cid)
        np.testing.assert_allclose(cluster.centroid, [0.0, 1.0])

    def test_update_member_count_and_summary(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0, 0.0])
        store.update_cluster(cid, summary="hello", member_count=42)
        cluster = store.get_cluster(cid)
        assert cluster.summary == "hello"
        assert cluster.member_count == 42

    def test_missing_id_raises(self, store):
        with pytest.raises(ValueError, match="does not exist"):
            store.update_cluster(9999, label="x")

    def test_invalid_centroid_raises(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0])
        with pytest.raises(ValueError, match="non-empty"):
            store.update_cluster(cid, centroid=[])


class TestAssignCluster:
    def test_assign_and_clear(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0, 0.0])
        mid = store.add_message(_msg("hello"))
        store.assign_cluster(mid, cid)

        # 부착됨을 messages.cluster_id로 확인
        with sqlite3.connect(store._db_path) as conn:
            row = conn.execute(
                "SELECT cluster_id FROM messages WHERE id = ?", (mid,)
            ).fetchone()
        assert row[0] == cid

        # None으로 부착 해제
        store.assign_cluster(mid, None)
        with sqlite3.connect(store._db_path) as conn:
            row = conn.execute(
                "SELECT cluster_id FROM messages WHERE id = ?", (mid,)
            ).fetchone()
        assert row[0] is None

    def test_missing_message_raises(self, store):
        with pytest.raises(ValueError, match="does not exist"):
            store.assign_cluster(9999, 1)


class TestGetMessagesForCluster:
    def test_returns_members_in_order(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0])
        m1 = store.add_message(_msg("first"))
        m2 = store.add_message(_msg("second"))
        m3 = store.add_message(_msg("not in cluster"))
        store.assign_cluster(m1, cid)
        store.assign_cluster(m2, cid)
        # m3 unassigned

        msgs = store.get_messages_for_cluster(cid)
        assert [m.content for m in msgs] == ["first", "second"]
        # m3는 포함되지 않음
        assert m3 not in [getattr(m, "id", None) for m in msgs]

    def test_empty_cluster(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0])
        assert store.get_messages_for_cluster(cid) == []


class TestGetUnclusteredWithEmbeddings:
    def test_returns_only_with_embedding_and_no_cluster(self, store):
        # m1: 임베딩 있고 cluster 없음 → 포함
        m1 = store.add_message(_msg("a"))
        store.add_embedding(m1, [1.0, 0.0])

        # m2: 임베딩 없음 → 제외
        store.add_message(_msg("b"))

        # m3: 임베딩 있고 cluster 부착됨 → 제외
        cid = store.create_cluster(label="x", centroid=[1.0, 0.0])
        m3 = store.add_message(_msg("c"))
        store.add_embedding(m3, [1.0, 0.0])
        store.assign_cluster(m3, cid)

        results = store.get_unclustered_with_embeddings()
        assert len(results) == 1
        mid, msg, emb = results[0]
        assert mid == m1
        assert msg.content == "a"
        np.testing.assert_allclose(emb, [1.0, 0.0])

    def test_empty(self, store):
        assert store.get_unclustered_with_embeddings() == []

    def test_returns_in_id_order(self, store):
        m1 = store.add_message(_msg("a"))
        m2 = store.add_message(_msg("b"))
        store.add_embedding(m1, [1.0, 0.0])
        store.add_embedding(m2, [0.0, 1.0])

        results = store.get_unclustered_with_embeddings()
        assert [r[0] for r in results] == [m1, m2]


class TestGetMessageWithEmbedding:
    def test_with_embedding(self, store):
        mid = store.add_message(_msg("x"))
        store.add_embedding(mid, [1.0, 2.0])
        result = store.get_message_with_embedding(mid)
        assert result is not None
        msg, emb = result
        assert msg.content == "x"
        np.testing.assert_allclose(emb, [1.0, 2.0])

    def test_without_embedding_returns_msg_none(self, store):
        mid = store.add_message(_msg("x"))
        result = store.get_message_with_embedding(mid)
        assert result is not None
        msg, emb = result
        assert msg.content == "x"
        assert emb is None

    def test_missing_returns_none(self, store):
        assert store.get_message_with_embedding(9999) is None
