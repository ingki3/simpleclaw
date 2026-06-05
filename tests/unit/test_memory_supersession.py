"""Regression tests for stale event supersession in long-term memory."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from simpleclaw.memory.active_projects import ActiveProject, filter_active
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
from simpleclaw.memory.supersession import is_expired_event_memory


def test_user_correction_supersedes_insight_and_archives_read_model(tmp_path):
    """A corrected topic must not be promoted back into active memory_items."""
    store = ConversationStore(tmp_path / "superseded-insight.db")
    now = datetime(2026, 6, 5, 23, 0, 0)
    corrected = InsightMeta(
        topic="KSAI 포럼 모니터링",
        text="KSAI 포럼은 2026-05-29에 종료됐고 더 이상 모니터링 대상이 아닙니다.",
        evidence_count=4,
        confidence=0.95,
        first_seen=now - timedelta(days=20),
        last_seen=now,
        superseded_at=now,
        superseded_by="manual_correction",
        correction_reason="user said the event already ended",
    )

    active = sync_insights_to_memory_items(
        store,
        [corrected],
        promotion_threshold=3,
        now=now,
    )

    assert active == []
    item = store.get_memory_item_by_source("insight_store", "insight:ksai포럼모니터링")
    assert item is not None
    assert item.status is MemoryItemStatus.ARCHIVED
    assert item.metadata["superseded_at"] == now.isoformat()
    assert item.metadata["superseded_by"] == "manual_correction"


def test_expired_event_active_project_is_excluded_from_sync_and_filter(tmp_path):
    """One-off events after their date should not remain active proactive context."""
    store = ConversationStore(tmp_path / "expired-project.db")
    now = datetime(2026, 6, 5, 23, 0, 0)
    ksai = ActiveProject(
        name="KSAI 포럼 1회",
        role="행사 모니터링",
        recent_summary="2026-05-29 KSAI 포럼 1회 일정을 계속 모니터링합니다.",
        first_seen=now - timedelta(days=20),
        last_seen=now,
    )

    assert is_expired_event_memory(ksai.name + " " + ksai.recent_summary, now=now)
    assert filter_active({"ksai": ksai}, window_days=30, now=now) == []
    active = sync_active_projects_to_memory_items(
        store,
        [ksai],
        window_days=30,
        now=now,
    )

    assert active == []
    item = store.get_memory_item_by_source("active_projects", "active_project:ksai포럼1회")
    assert item is not None
    assert item.status is MemoryItemStatus.ARCHIVED
    assert item.metadata["expired_event"] is True


def test_manual_correction_memory_is_retrieved_before_stale_raw_memory(tmp_path):
    """Decision/correction memory_items should outrank stale raw memories with same embedding."""
    store = ConversationStore(tmp_path / "correction-rerank.db")
    stale = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="KSAI 포럼 1회 모니터링을 계속 제안합니다.",
        confidence=0.95,
        importance=1.0,
        embedding=[1.0, 0.0],
    )
    correction = store.create_memory_item(
        item_type=MemoryItemType.DECISION,
        text="결정: KSAI 포럼 1회는 2026-05-29에 종료되어 더 이상 proactive 제안 대상이 아닙니다.",
        confidence=0.9,
        importance=0.8,
        embedding=[1.0, 0.0],
        metadata={"manual_correction": True, "supersedes": [stale.id]},
    )

    hits = store.search_memory_items([1.0, 0.0], k=2, min_confidence=0.7)

    assert [item.id for item, _score in hits] == [correction.id, stale.id]


def test_pending_and_rejected_suggestions_do_not_become_active_memory_items(tmp_path):
    """Unreviewed/rejected suggestions must stay out of retrieval/prompt context."""
    store = ConversationStore(tmp_path / "suggestion-status.db")
    pending = SuggestionMeta(
        id="pending-1",
        topic="KSAI 포럼",
        text="KSAI 포럼 모니터링을 계속 제안합니다.",
        confidence=0.92,
        status="pending",
    )
    rejected = SuggestionMeta(
        id="rejected-1",
        topic="KSAI 포럼 거절",
        text="KSAI 포럼 모니터링을 계속 제안합니다.",
        confidence=0.92,
        status="rejected",
        reject_reason="event already ended",
    )

    pending_item = sync_suggestion_to_memory_item(store, pending)
    rejected_item = sync_suggestion_to_memory_item(store, rejected)

    assert pending_item is not None
    assert rejected_item is not None
    assert pending_item.status is MemoryItemStatus.ARCHIVED
    assert rejected_item.status is MemoryItemStatus.ARCHIVED


def test_expired_event_cluster_summary_is_archived_not_retrieved(tmp_path):
    """Cluster summaries can also carry stale event signals; keep them inactive."""
    store = ConversationStore(tmp_path / "expired-cluster.db")
    item = sync_cluster_summary_to_memory_item(
        store,
        ClusterRecord(
            id=42,
            label="KSAI 포럼",
            centroid=np.array([0.5, 0.5], dtype=np.float32),
            summary="2026-05-29 KSAI 포럼 1회 일정을 계속 모니터링합니다.",
            member_count=5,
            updated_at=datetime(2026, 6, 5, 12, 0, 0),
        ),
        now=datetime(2026, 6, 5, 23, 0, 0),
    )

    assert item.status is MemoryItemStatus.ARCHIVED
    assert item.embedding is None
    assert item.metadata["expired_event"] is True


@pytest.mark.parametrize(
    "text",
    [
        "2026-05-29 KSAI 포럼 1회 일정",
        "KSAI 포럼은 2026년 5월 29일에 열렸습니다.",
        "지난 5월 29일 포럼 일정 모니터링",
    ],
)
def test_expired_event_detector_understands_common_korean_date_forms(text):
    assert is_expired_event_memory(text, now=datetime(2026, 6, 5, 12, 0, 0))
