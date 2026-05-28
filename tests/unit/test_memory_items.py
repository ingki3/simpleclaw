"""ConversationStore memory_items 저장소/인덱스 단위 테스트.

Phase 1 장기기억 read model은 기존 messages/semantic_clusters 스키마에 더해지는
additive migration이어야 하며, MEMORY.md/USER.md 파서 교체 전에도 독립 CRUD로
검증 가능해야 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import (
    ConversationMessage,
    MemoryItemStatus,
    MemoryItemType,
    MessageRole,
)


def _table_names(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }


def _index_names(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            row[1]
            for row in conn.execute("PRAGMA index_list(memory_items)").fetchall()
        }


def test_memory_items_schema_and_indexes_are_created(tmp_path):
    """ConversationStore 초기화는 memory_items 테이블/조회 인덱스를 자동 생성한다."""
    db_path = tmp_path / "memory-items.db"

    ConversationStore(db_path)

    assert "memory_items" in _table_names(db_path)
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_items)").fetchall()}
    assert {
        "id", "type", "text", "source", "source_ref", "confidence", "importance",
        "status", "first_seen", "last_seen", "last_accessed", "embedding",
    } <= cols
    indexes = _index_names(db_path)
    assert "idx_memory_items_type_status_updated" in indexes
    assert "idx_memory_items_source" in indexes


def test_create_get_and_list_memory_item_roundtrip(tmp_path):
    """생성된 memory item은 단건 조회와 list 조회에서 동일한 모델로 복원된다."""
    store = ConversationStore(tmp_path / "memory-items.db")

    created = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="Hyungjoo는 SimpleClaw의 memory_items Phase 1을 진행 중입니다.",
        source="operator-plan",
        source_msg_ids=[3, 1, 3],
        metadata={"phase": 1, "reviewed": False},
    )

    assert created.id > 0
    assert created.type is MemoryItemType.MEMORY
    assert created.status is MemoryItemStatus.ACTIVE
    assert created.source_msg_ids == [1, 3]
    assert created.metadata == {"phase": 1, "reviewed": False}
    assert store.get_memory_item(created.id) == created
    assert store.list_memory_items() == [created]


def test_list_memory_items_filters_and_sorts_by_updated_desc(tmp_path):
    """type/status/source 필터는 조합 가능하고 기본 정렬은 updated_at 내림차순이다."""
    store = ConversationStore(tmp_path / "memory-items.db")

    first = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="오래된 memory",
        source="dreaming",
    )
    second = store.create_memory_item(
        item_type=MemoryItemType.USER,
        text="사용자는 간결한 답변을 선호합니다.",
        source="dreaming",
    )
    third = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="최신 memory",
        source="manual",
    )

    assert [item.id for item in store.list_memory_items()] == [third.id, second.id, first.id]
    assert [item.id for item in store.list_memory_items(item_type=MemoryItemType.MEMORY)] == [
        third.id,
        first.id,
    ]
    assert [item.id for item in store.list_memory_items(source="dreaming")] == [
        second.id,
        first.id,
    ]
    assert store.list_memory_items(status=MemoryItemStatus.ARCHIVED) == []


def test_update_memory_item_changes_updated_at_but_keeps_id(tmp_path):
    """부분 update는 id/created_at을 유지하고 updated_at 및 지정 필드만 갱신한다."""
    store = ConversationStore(tmp_path / "memory-items.db")
    created = store.create_memory_item(
        item_type=MemoryItemType.SUGGESTION,
        text="초안",
        source="admin",
        metadata={"version": 1},
    )

    updated = store.update_memory_item(
        created.id,
        text="검토 완료 초안",
        item_type=MemoryItemType.INSIGHT,
        metadata={"version": 2, "accepted": True},
    )

    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert updated.type is MemoryItemType.INSIGHT
    assert updated.text == "검토 완료 초안"
    assert updated.metadata == {"version": 2, "accepted": True}


def test_archive_memory_item_excludes_default_active_list(tmp_path):
    """archive는 삭제가 아니라 archived_at/status 전환이며 기본 active 목록에서 제외된다."""
    store = ConversationStore(tmp_path / "memory-items.db")
    keep = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="활성 항목",
        source="manual",
    )
    stale = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="아카이브 대상",
        source="manual",
    )

    archived = store.archive_memory_item(stale.id)

    assert archived.id == stale.id
    assert archived.status is MemoryItemStatus.ARCHIVED
    assert isinstance(archived.archived_at, datetime)
    assert store.list_memory_items() == [keep]
    assert [item.id for item in store.list_memory_items(include_archived=True)] == [
        archived.id,
        keep.id,
    ]


def test_memory_items_migration_is_additive_for_legacy_db(tmp_path):
    """레거시 messages DB도 기존 데이터를 보존한 채 memory_items만 추가 마이그레이션된다."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL, "
            "token_count INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE semantic_clusters ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL DEFAULT '', "
            "centroid BLOB NOT NULL, summary TEXT NOT NULL DEFAULT '', "
            "member_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO messages (role, content, timestamp, token_count) VALUES (?, ?, ?, ?)",
            (MessageRole.USER.value, "legacy", datetime.now().isoformat(), 1),
        )

    store = ConversationStore(db_path)

    assert store.count() == 1
    store.add_message(ConversationMessage(role=MessageRole.ASSISTANT, content="new"))
    assert store.count() == 2
    created = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="레거시 DB 위에 추가된 장기기억 항목",
        source="migration-test",
    )
    assert store.get_memory_item(created.id) == created

def test_memory_item_embedding_search_filters_and_updates_last_accessed(tmp_path):
    """active/confidence 필터를 통과한 memory item만 cosine search 되고 hit 메타를 갱신한다."""
    store = ConversationStore(tmp_path / "memory-items-search.db")
    keep = store.create_memory_item(
        item_type=MemoryItemType.ACCEPTED_USER_INSIGHT,
        text="사용자는 한국어 존댓말을 선호합니다.",
        source="insight_store",
        source_ref="topic:korean-tone",
        confidence=0.9,
        importance=0.8,
        embedding=[1.0, 0.0],
    )
    store.create_memory_item(
        item_type=MemoryItemType.PREFERENCE,
        text="낮은 confidence 항목",
        confidence=0.3,
        embedding=[1.0, 0.0],
    )
    archived = store.create_memory_item(
        item_type=MemoryItemType.DECISION,
        text="아카이브된 결정",
        confidence=0.95,
        status=MemoryItemStatus.ARCHIVED,
        embedding=[1.0, 0.0],
    )

    hits = store.search_memory_items([1.0, 0.0], k=5, min_confidence=0.7)

    assert [(item.id, round(score, 3)) for item, score in hits] == [(keep.id, 1.0)]
    assert store.get_memory_item(archived.id).status is MemoryItemStatus.ARCHIVED
    accessed = store.mark_memory_item_accessed(keep.id)
    assert accessed.last_accessed is not None


def test_upsert_memory_item_is_idempotent_by_source_ref_and_preserves_embedding(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-upsert.db")

    created = store.upsert_memory_item(
        item_type=MemoryItemType.ACCEPTED_USER_INSIGHT,
        text="초기 인사이트",
        source="insight_store",
        source_ref="insight:korean-tone",
        confidence=0.7,
        importance=0.7,
        embedding=[1.0, 0.0],
        source_msg_ids=[2],
        metadata={"version": 1},
    )
    updated = store.upsert_memory_item(
        item_type=MemoryItemType.ACCEPTED_USER_INSIGHT,
        text="갱신된 인사이트",
        source="insight_store",
        source_ref="insight:korean-tone",
        confidence=0.9,
        importance=0.8,
        source_msg_ids=[3, 2],
        metadata={"version": 2},
    )

    assert updated.id == created.id
    assert updated.text == "갱신된 인사이트"
    assert updated.confidence == 0.9
    assert updated.importance == 0.8
    assert updated.source_msg_ids == [2, 3]
    assert updated.metadata == {"version": 2}
    assert updated.embedding is not None
    assert updated.embedding.tolist() == [1.0, 0.0]
    assert len(store.list_memory_items(include_archived=True)) == 1

    archived = store.upsert_memory_item(
        item_type=MemoryItemType.ACCEPTED_USER_INSIGHT,
        text="갱신된 인사이트",
        source="insight_store",
        source_ref="insight:korean-tone",
        status=MemoryItemStatus.ARCHIVED,
    )
    assert archived.id == created.id
    assert archived.status is MemoryItemStatus.ARCHIVED
    assert store.list_memory_items() == []


def test_upsert_memory_item_replaces_embedding_when_provided(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-upsert-embedding.db")
    created = store.upsert_memory_item(
        item_type=MemoryItemType.CLUSTER_SUMMARY,
        text="cluster",
        source="semantic_cluster",
        source_ref="cluster:1",
        embedding=[1.0, 0.0],
    )

    updated = store.upsert_memory_item(
        item_type=MemoryItemType.CLUSTER_SUMMARY,
        text="cluster updated",
        source="semantic_cluster",
        source_ref="cluster:1",
        embedding=[0.0, 1.0],
    )

    assert updated.id == created.id
    assert updated.embedding is not None
    assert updated.embedding.tolist() == [0.0, 1.0]
