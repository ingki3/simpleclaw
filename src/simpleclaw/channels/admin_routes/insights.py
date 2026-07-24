"""Admin API insights route handlers."""

from __future__ import annotations

# ruff: noqa: F401
import copy
import json
import logging
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web

from simpleclaw.channels.admin_api import (
    _BACKEND_LABELS,
    AREA_TO_YAML_KEY,
    _actor_from,
    _audit_to_dict,
    _deep_merge,
    _detect_backend,
    _entry_to_dict,
    _flatten_keys,
    _get_dotted,
    _http_test_send,
    _json_error,
    _json_ok,
    _last_rotated_for,
    _list_backend_keys,
    _load_pending,
    _mask_for_response,
    _pending_changes_path,
    _policy_to_dict,
    _project,
    _project_subtree,
    _RevealEntry,
    _rotate_master_key,
    _save_pending,
    _set_dotted,
    _truthy_query,
)
from simpleclaw.channels.admin_audit import AuditEntry
from simpleclaw.channels.admin_policy import (
    HOT,
    PROCESS_RESTART,
    classify_keys,
    validate_patch,
)
from simpleclaw.memory.memory_items_sync import sync_suggestion_to_memory_item
from simpleclaw.memory.suggestions import TERMINAL_STATUSES
from simpleclaw.security.secrets import EncryptedFileBackend, SecretsError

logger = logging.getLogger(__name__)

def register_routes(app: web.Application, server: Any, prefix: str) -> None:
    """Insight source/list/blocklist routes를 등록한다."""
    app.router.add_get(
        f"{prefix}/memory/insights/{{topic}}/sources",
        server._wrap(server._handle_get_insight_sources),
    )
    app.router.add_get(
        f"{prefix}/memory/insights",
        server._wrap(server._handle_list_insights),
    )
    app.router.add_get(
        f"{prefix}/memory/blocklist",
        server._wrap(server._handle_list_blocklist),
    )

HANDLERS = ('_handle_get_insight_sources', '_serialize_insight', '_handle_list_insights', '_handle_list_blocklist',)

async def _handle_get_insight_sources(
    self, request: web.Request
) -> web.Response:
    """``GET /admin/v1/memory/insights/{topic}/sources``.

    주어진 topic (원문 또는 정규형) 의 인사이트 메타를 sidecar 에서 찾고,
    ``source_msg_ids`` 가 가리키는 메시지를 ``ConversationStore`` 에서 조회해
    Admin UI 에 노출할 형태로 반환한다.

    실패 응답:
    - 503: ``conversation_store`` 또는 ``insight_store`` 가 주입되지 않음
      (Admin API 가 메모리 스택 없이 부팅된 환경 — silent 404 보다 명시적인
      503 이 운영 진단에 유리하다).
    - 404: 해당 topic 의 인사이트가 sidecar 에 없음.
    - 422: topic path 가 비어 있거나 정규화 후 빈 문자열.
    """
    if self._conversation_store is None or self._insight_store is None:
        return _json_error(
            503,
            "Insight source linkage is not configured on this server",
        )

    # match_info 는 aiohttp 가 URL 디코딩한 값을 돌려준다. 양 끝 공백 트림.
    topic_param = (request.match_info.get("topic") or "").strip()
    if not topic_param:
        return _json_error(422, "topic path parameter is required")

    meta = self._insight_store.find_by_topic(topic_param)
    if meta is None:
        return _json_error(404, f"Insight not found for topic: {topic_param}")

    # source 메시지가 없으면 빈 배열을 반환 — UI 에서 "근거 메시지 없음" 처리.
    rows = self._conversation_store.get_messages_by_ids(meta.source_msg_ids)
    sources = [
        {
            "id": mid,
            "role": msg.role.value,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
            "channel": msg.channel,
            "token_count": msg.token_count,
        }
        for mid, msg in rows
    ]

    return _json_ok({
        "topic": meta.topic,
        "text": meta.text,
        "evidence_count": meta.evidence_count,
        "confidence": meta.confidence,
        "first_seen": meta.first_seen.isoformat(),
        "last_seen": meta.last_seen.isoformat(),
        "start_msg_id": meta.start_msg_id,
        "end_msg_id": meta.end_msg_id,
        "source_msg_ids": list(meta.source_msg_ids),
        "sources": sources,
    })

def _serialize_insight(self, meta) -> dict:
    """``InsightMeta`` 를 JSON 응답 dict 로 변환.

    Active/Archive 탭에서 InsightCard 가 직접 소비하는 필드 집합:
    topic/text/confidence/evidence/last_seen/start_msg_id/end_msg_id/
    archived_at. last_seen 은 카드의 메타 라인, archived_at 은 탭 분리에
    쓰인다.
    """
    return {
        "topic": meta.topic,
        "text": meta.text,
        "evidence_count": meta.evidence_count,
        "confidence": meta.confidence,
        "first_seen": meta.first_seen.isoformat(),
        "last_seen": meta.last_seen.isoformat(),
        "start_msg_id": meta.start_msg_id,
        "end_msg_id": meta.end_msg_id,
        "source_msg_ids": list(meta.source_msg_ids),
        "archived_at": (
            meta.archived_at.isoformat() if meta.archived_at else None
        ),
    }

async def _handle_list_insights(self, request: web.Request) -> web.Response:
    """``GET /admin/v1/memory/insights``.

    Query params:
    - ``status``: ``active`` (default — archived_at is None) /
      ``archived`` (archived_at is not None) / ``all`` (debugging).

    Response:
    ``{"insights": [...], "total": N, "active_count": A, "archived_count": B}``

    503 if ``insight_store`` 가 주입되지 않음 — Admin API 가 메모리 스택
    없이 부팅된 환경 (silent 404 보다 명시적인 503 이 운영 진단에 유리).
    """
    if self._insight_store is None:
        return _json_error(
            503, "Insight store is not configured on this server"
        )

    status_filter = (request.query.get("status") or "active").strip().lower()
    all_items = list(self._insight_store.load().values())
    # 정렬: last_seen 내림차순 — 최근 관측이 위로. 운영자 멘탈 모델상 가장
    # 자연스럽다 (Active 는 새로 강화된 항목 우선, Archive 는 가장 최근에
    # 잠든 항목 우선).
    all_items.sort(key=lambda m: m.last_seen, reverse=True)
    if status_filter == "all":
        items = all_items
    elif status_filter == "archived":
        items = [m for m in all_items if m.is_archived()]
    else:  # default 'active'
        items = [m for m in all_items if not m.is_archived()]

    return _json_ok({
        "insights": [self._serialize_insight(m) for m in items],
        "total": len(items),
        "active_count": sum(1 for m in all_items if not m.is_archived()),
        "archived_count": sum(1 for m in all_items if m.is_archived()),
    })

async def _handle_list_blocklist(self, request: web.Request) -> web.Response:
    """``GET /admin/v1/memory/blocklist``.

    BIZ-79 의 ``BlocklistStore`` 는 정규형 topic 한 줄당 ``{topic,
    topic_key, reason, blocked_at}`` 를 저장한다. 응답 형태:

    ``{"entries": [{topic, topic_key, reason, blocked_at}, ...],
       "total": N}``

    503 if ``blocklist_store`` 가 주입되지 않음. 정렬은 ``blocked_at``
    내림차순 — 운영자가 가장 최근 결정의 컨텍스트를 빠르게 회수.
    """
    if self._blocklist_store is None:
        return _json_error(
            503, "Blocklist store is not configured on this server"
        )

    entries = list(self._blocklist_store.load().values())
    # blocked_at 은 ISO 문자열 — string 비교가 ISO 형식에서 시간순 정렬과
    # 일치한다. 누락된 필드는 빈 문자열로 폴백해 정렬이 깨지지 않도록.
    entries.sort(key=lambda e: e.get("blocked_at") or "", reverse=True)
    return _json_ok({
        "entries": [
            {
                "topic": e.get("topic", ""),
                "topic_key": e.get("topic_key", ""),
                "reason": e.get("reason", ""),
                "blocked_at": e.get("blocked_at"),
            }
            for e in entries
        ],
        "total": len(entries),
    })
