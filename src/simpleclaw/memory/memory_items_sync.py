"""Sync sidecar long-term memory sources into the memory_items read model."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from simpleclaw.memory.active_projects import ActiveProject, normalize_name
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.insights import InsightMeta, is_promoted, normalize_topic
from simpleclaw.memory.models import ClusterRecord, MemoryItem, MemoryItemStatus, MemoryItemType
from simpleclaw.memory.suggestions import SuggestionMeta

logger = logging.getLogger(__name__)

INSIGHT_SOURCE = "insight_store"
SUGGESTION_SOURCE = "suggestion_store"
ACTIVE_PROJECT_SOURCE = "active_projects"
CLUSTER_SOURCE = "semantic_cluster"
DEFAULT_HIGH_CONFIDENCE = 0.7


def _now(now: datetime | None) -> datetime:
    return now or datetime.now()


def _insight_source_ref(topic: str) -> str:
    return f"insight:{normalize_topic(topic)}"


def _project_source_ref(name: str) -> str:
    return f"active_project:{normalize_name(name)}"


def _cluster_source_ref(cluster_id: int) -> str:
    return f"cluster:{int(cluster_id)}"


def _classify_insight(meta: InsightMeta) -> MemoryItemType:
    haystack = f"{meta.topic} {meta.text}".lower()
    topic = meta.topic.strip().lower()
    text = meta.text.strip().lower()
    if topic.startswith(("decision:", "결정:")) or text.startswith("결정:") or "decision" in haystack:
        return MemoryItemType.DECISION
    if (
        topic.startswith(("preference:", "선호:"))
        or text.startswith("선호:")
        or "preference" in topic
    ):
        return MemoryItemType.PREFERENCE
    return MemoryItemType.ACCEPTED_USER_INSIGHT


def sync_insights_to_memory_items(
    store: ConversationStore,
    insights: Iterable[InsightMeta],
    *,
    promotion_threshold: int,
    high_confidence: float = DEFAULT_HIGH_CONFIDENCE,
    now: datetime | None = None,
) -> list[MemoryItem]:
    """Upsert accepted/promoted/high-confidence insights and archive inactive ones."""
    ts = _now(now)
    active_items: list[MemoryItem] = []
    seen_refs: set[str] = set()
    for meta in insights:
        source_ref = _insight_source_ref(meta.topic)
        if source_ref == "insight:":
            continue
        seen_refs.add(source_ref)
        item_type = _classify_insight(meta)
        should_be_active = (
            not meta.is_archived()
            and meta.confidence >= high_confidence
            and (
                item_type in {MemoryItemType.DECISION, MemoryItemType.PREFERENCE}
                or is_promoted(meta, promotion_threshold)
            )
        )
        item = store.upsert_memory_item(
            item_type=item_type,
            text=meta.text,
            source=INSIGHT_SOURCE,
            source_ref=source_ref,
            confidence=meta.confidence,
            importance=min(1.0, max(0.0, meta.confidence)),
            status=(
                MemoryItemStatus.ACTIVE
                if should_be_active
                else MemoryItemStatus.ARCHIVED
            ),
            first_seen=meta.first_seen,
            last_seen=meta.last_seen or ts,
            source_msg_ids=meta.source_msg_ids,
            metadata={
                "topic": meta.topic,
                "evidence_count": meta.evidence_count,
                "start_msg_id": meta.start_msg_id,
                "end_msg_id": meta.end_msg_id,
                "sync_reason": (
                    "active" if should_be_active else "inactive_or_low_confidence"
                ),
            },
        )
        if item.status is MemoryItemStatus.ACTIVE:
            active_items.append(item)
    for item in store.list_memory_items(source=INSIGHT_SOURCE, include_archived=True):
        if item.source_ref not in seen_refs and item.status is MemoryItemStatus.ACTIVE:
            store.archive_memory_item(item.id)
    return active_items


def sync_suggestion_to_memory_item(
    store: ConversationStore,
    suggestion: SuggestionMeta,
    *,
    now: datetime | None = None,
) -> MemoryItem | None:
    """Reflect operator-reviewed suggestion status into memory_items."""
    source_ref = _insight_source_ref(suggestion.topic)
    if source_ref == "insight:":
        return None
    status = (
        MemoryItemStatus.ACTIVE
        if suggestion.status in {"accepted", "edited"}
        else MemoryItemStatus.ARCHIVED
    )
    text = suggestion.applied_text if status is MemoryItemStatus.ACTIVE else suggestion.text
    return store.upsert_memory_item(
        item_type=MemoryItemType.ACCEPTED_USER_INSIGHT,
        text=text,
        source=SUGGESTION_SOURCE,
        source_ref=source_ref,
        confidence=suggestion.confidence,
        importance=min(1.0, max(0.0, suggestion.confidence)),
        status=status,
        first_seen=suggestion.created_at,
        last_seen=_now(now),
        source_msg_ids=suggestion.source_msg_ids,
        metadata={
            "topic": suggestion.topic,
            "suggestion_id": suggestion.id,
            "suggestion_status": suggestion.status,
            "evidence_count": suggestion.evidence_count,
        },
    )


def _render_project_text(project: ActiveProject) -> str:
    parts = [f"프로젝트: {project.name}"]
    if project.role:
        parts.append(f"역할: {project.role}")
    if project.recent_summary:
        parts.append(f"최근 활동: {project.recent_summary}")
    parts.append(f"last_seen: {project.last_seen.strftime('%Y-%m-%d')}")
    return "\n".join(parts)


def sync_active_projects_to_memory_items(
    store: ConversationStore,
    projects: Iterable[ActiveProject],
    *,
    window_days: int,
    now: datetime | None = None,
) -> list[MemoryItem]:
    """Upsert window-active projects and archive window-expired projects."""
    ts = _now(now)
    cutoff = ts - timedelta(days=window_days)
    active: list[MemoryItem] = []
    seen_refs: set[str] = set()
    for project in projects:
        source_ref = _project_source_ref(project.name)
        if source_ref == "active_project:":
            continue
        seen_refs.add(source_ref)
        is_active = window_days > 0 and project.last_seen >= cutoff
        item = store.upsert_memory_item(
            item_type=MemoryItemType.ACTIVE_PROJECT,
            text=_render_project_text(project),
            source=ACTIVE_PROJECT_SOURCE,
            source_ref=source_ref,
            confidence=0.8 if is_active else 0.0,
            importance=0.8 if is_active else 0.0,
            status=(MemoryItemStatus.ACTIVE if is_active else MemoryItemStatus.ARCHIVED),
            first_seen=project.first_seen,
            last_seen=project.last_seen,
            metadata={
                "name": project.name,
                "role": project.role,
                "window_days": window_days,
            },
        )
        if item.status is MemoryItemStatus.ACTIVE:
            active.append(item)
    for item in store.list_memory_items(source=ACTIVE_PROJECT_SOURCE, include_archived=True):
        if item.source_ref not in seen_refs and item.status is MemoryItemStatus.ACTIVE:
            store.archive_memory_item(item.id)
    return active


def sync_cluster_summary_to_memory_item(
    store: ConversationStore,
    cluster: ClusterRecord,
    *,
    now: datetime | None = None,
) -> MemoryItem:
    """Upsert one semantic cluster summary into memory_items."""
    ts = _now(now)
    summary = (cluster.summary or "").strip()
    label = (cluster.label or "").strip()
    text = f"[{label}]\n{summary}" if label and summary else summary or label
    is_active = bool(summary)
    return store.upsert_memory_item(
        item_type=MemoryItemType.CLUSTER_SUMMARY,
        text=text,
        source=CLUSTER_SOURCE,
        source_ref=_cluster_source_ref(cluster.id),
        confidence=0.75 if is_active else 0.0,
        importance=min(1.0, max(0.0, cluster.member_count / 10.0)),
        status=MemoryItemStatus.ACTIVE if is_active else MemoryItemStatus.ARCHIVED,
        first_seen=cluster.updated_at,
        last_seen=ts,
        embedding=cluster.centroid if is_active else None,
        metadata={
            "cluster_id": cluster.id,
            "label": cluster.label,
            "member_count": cluster.member_count,
        },
    )
