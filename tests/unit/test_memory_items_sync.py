"""memory_items read model sync helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from simpleclaw.memory.active_projects import ActiveProject
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.insights import InsightMeta
from simpleclaw.memory.memory_items_sync import (
    sync_active_projects_to_memory_items,
    sync_cluster_summary_to_memory_item,
    sync_insights_to_memory_items,
    sync_suggestion_to_memory_item,
)
from simpleclaw.memory.models import ClusterRecord, MemoryItemStatus, MemoryItemType
from simpleclaw.memory.suggestions import SuggestionMeta


def test_sync_promoted_insights_archives_low_confidence_and_unpromoted(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-sync.db")
    now = datetime(2026, 5, 28, 12, 0, 0)
    promoted = InsightMeta(
        topic="한국어 말투 선호",
        text="사용자는 한국어 존댓말 응답을 선호합니다.",
        evidence_count=3,
        confidence=0.8,
        first_seen=now - timedelta(days=2),
        last_seen=now,
        source_msg_ids=[3, 1, 3],
    )
    low_conf = InsightMeta(
        topic="단발 관심사",
        text="사용자가 러닝화를 언급했습니다.",
        evidence_count=1,
        confidence=0.4,
        first_seen=now,
        last_seen=now,
    )

    active = sync_insights_to_memory_items(
        store,
        [promoted, low_conf],
        promotion_threshold=3,
        now=now,
    )

    assert [item.text for item in active] == [promoted.text]
    item = active[0]
    assert item.type is MemoryItemType.ACCEPTED_USER_INSIGHT
    assert item.source == "insight_store"
    assert item.source_ref == "insight:한국어말투선호"
    assert item.source_msg_ids == [1, 3]
    assert item.status is MemoryItemStatus.ACTIVE
    all_items = store.list_memory_items(include_archived=True)
    low_item = next(i for i in all_items if i.source_ref == "insight:단발관심사")
    assert low_item.status is MemoryItemStatus.ARCHIVED
    assert store.list_memory_items() == [item]


def test_sync_high_confidence_decision_and_preference_types(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-kinds.db")
    now = datetime(2026, 5, 28, 12, 0, 0)
    decision = InsightMeta(
        topic="decision: PR base",
        text="결정: BIZ-308 PR은 BIZ-307 브랜치를 base로 둡니다.",
        evidence_count=2,
        confidence=0.9,
        first_seen=now,
        last_seen=now,
    )
    preference = InsightMeta(
        topic="preference: concise",
        text="선호: 사용자는 결과 요약을 간결하게 받는 것을 선호합니다.",
        evidence_count=2,
        confidence=0.85,
        first_seen=now,
        last_seen=now,
    )

    synced = sync_insights_to_memory_items(
        store,
        [decision, preference],
        promotion_threshold=5,
        now=now,
    )

    assert {item.type for item in synced} == {
        MemoryItemType.DECISION,
        MemoryItemType.PREFERENCE,
    }
    assert {item.status for item in synced} == {MemoryItemStatus.ACTIVE}



def test_sync_reviewed_suggestion_accept_edit_and_reject(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-suggestions.db")
    created = datetime(2026, 5, 28, 12, 0, 0)
    accepted = SuggestionMeta(
        id="sug-1",
        topic="응답 방식",
        text="사용자는 결론을 먼저 듣는 방식을 선호합니다.",
        confidence=0.82,
        evidence_count=2,
        source_msg_ids=[5, 4, 5],
        status="accepted",
        created_at=created,
    )

    item = sync_suggestion_to_memory_item(store, accepted, now=created)

    assert item is not None
    assert item.type is MemoryItemType.ACCEPTED_USER_INSIGHT
    assert item.source == "suggestion_store"
    assert item.source_ref == "insight:응답방식"
    assert item.text == accepted.text
    assert item.source_msg_ids == [4, 5]
    assert item.status is MemoryItemStatus.ACTIVE

    edited = SuggestionMeta(
        id="sug-1",
        topic="응답 방식",
        text=accepted.text,
        confidence=0.9,
        evidence_count=3,
        source_msg_ids=[7],
        status="edited",
        edited_text="사용자는 답변 첫 문단에 결론 요약을 원합니다.",
        created_at=created,
    )
    edited_item = sync_suggestion_to_memory_item(
        store, edited, now=created + timedelta(minutes=1)
    )

    assert edited_item is not None
    assert edited_item.id == item.id
    assert edited_item.text == edited.edited_text
    assert edited_item.confidence == 0.9
    assert edited_item.status is MemoryItemStatus.ACTIVE

    rejected = SuggestionMeta(
        id="sug-1",
        topic="응답 방식",
        text=accepted.text,
        confidence=0.9,
        evidence_count=3,
        status="rejected",
        created_at=created,
    )
    archived = sync_suggestion_to_memory_item(
        store, rejected, now=created + timedelta(minutes=2)
    )

    assert archived is not None
    assert archived.id == item.id
    assert archived.status is MemoryItemStatus.ARCHIVED
    assert store.list_memory_items() == []


def test_sync_active_projects_archives_items_outside_window(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-projects.db")
    now = datetime(2026, 5, 28, 12, 0, 0)
    current = ActiveProject(
        name="SimpleClaw",
        role="장기기억 개선",
        recent_summary="memory_items read model 동기화 작업 중",
        first_seen=now - timedelta(days=5),
        last_seen=now,
    )
    stale = ActiveProject(
        name="Old Project",
        role="완료됨",
        recent_summary="최근 활동 없음",
        first_seen=now - timedelta(days=30),
        last_seen=now - timedelta(days=20),
    )

    active = sync_active_projects_to_memory_items(
        store,
        [current, stale],
        window_days=7,
        now=now,
    )

    assert [item.source_ref for item in active] == ["active_project:simpleclaw"]
    assert active[0].type is MemoryItemType.ACTIVE_PROJECT
    assert "SimpleClaw" in active[0].text
    archived = next(
        item for item in store.list_memory_items(include_archived=True)
        if item.source_ref == "active_project:oldproject"
    )
    assert archived.status is MemoryItemStatus.ARCHIVED
    assert store.list_memory_items() == active



def test_sync_active_projects_archives_items_missing_from_sidecar(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-projects-missing.db")
    now = datetime(2026, 5, 28, 12, 0, 0)
    existing = store.upsert_memory_item(
        item_type=MemoryItemType.ACTIVE_PROJECT,
        text="프로젝트: 이전 작업",
        source="active_projects",
        source_ref="active_project:oldproject",
        status=MemoryItemStatus.ACTIVE,
    )

    active = sync_active_projects_to_memory_items(store, [], window_days=7, now=now)

    assert active == []
    archived = store.get_memory_item(existing.id)
    assert archived is not None
    assert archived.status is MemoryItemStatus.ARCHIVED


def test_sync_cluster_summary_upserts_with_embedding_and_archives_empty_summary(tmp_path):
    store = ConversationStore(tmp_path / "memory-items-cluster.db")
    now = datetime(2026, 5, 28, 12, 0, 0)
    cluster = ClusterRecord(
        id=42,
        label="메모리 검색",
        centroid=np.array([1.0, 0.0], dtype=np.float32),
        summary="장기기억 검색 통합과 read model 동기화 논의입니다.",
        member_count=7,
        updated_at=now,
    )

    item = sync_cluster_summary_to_memory_item(store, cluster, now=now)
    again = sync_cluster_summary_to_memory_item(
        store,
        ClusterRecord(
            id=42,
            label="메모리 검색",
            centroid=np.array([0.0, 1.0], dtype=np.float32),
            summary="갱신된 클러스터 요약입니다.",
            member_count=8,
            updated_at=now + timedelta(minutes=1),
        ),
        now=now + timedelta(minutes=1),
    )

    assert again.id == item.id
    assert again.source == "semantic_cluster"
    assert again.source_ref == "cluster:42"
    assert again.type is MemoryItemType.CLUSTER_SUMMARY
    assert again.status is MemoryItemStatus.ACTIVE
    assert again.text == "[메모리 검색]\n갱신된 클러스터 요약입니다."
    assert again.embedding is not None
    assert again.embedding.tolist() == [0.0, 1.0]
    assert len(store.list_memory_items(include_archived=True)) == 1

    archived = sync_cluster_summary_to_memory_item(
        store,
        ClusterRecord(
            id=42,
            label="메모리 검색",
            centroid=np.array([0.0, 1.0], dtype=np.float32),
            summary="",
            member_count=8,
            updated_at=now + timedelta(minutes=2),
        ),
        now=now + timedelta(minutes=2),
    )
    assert archived.status is MemoryItemStatus.ARCHIVED
    assert store.list_memory_items() == []
