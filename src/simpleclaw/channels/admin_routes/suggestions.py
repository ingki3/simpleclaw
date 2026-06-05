"""Admin API suggestions route handlers."""

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

from simpleclaw.channels.admin_audit import AuditEntry
from simpleclaw.channels.admin_policy import HOT, PROCESS_RESTART, classify_keys, validate_patch
from simpleclaw.channels.admin_api import (
    AREA_TO_YAML_KEY,
    _BACKEND_LABELS,
    _RevealEntry,
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
    _rotate_master_key,
    _save_pending,
    _set_dotted,
    _truthy_query,
)
from simpleclaw.memory.memory_items_sync import sync_suggestion_to_memory_item
from simpleclaw.memory.suggestions import TERMINAL_STATUSES
from simpleclaw.security.secrets import EncryptedFileBackend, SecretsError

logger = logging.getLogger(__name__)

def register_routes(app: web.Application, server: Any, prefix: str) -> None:
    """Memory suggestion review loop routes를 등록한다."""
    app.router.add_get(
        f"{prefix}/memory/suggestions",
        server._wrap(server._handle_list_suggestions),
    )
    app.router.add_get(
        f"{prefix}/memory/suggestions/{{sid}}/sources",
        server._wrap(server._handle_get_suggestion_sources),
    )
    app.router.add_post(
        f"{prefix}/memory/suggestions/{{sid}}/accept",
        server._wrap(server._handle_accept_suggestion),
    )
    app.router.add_post(
        f"{prefix}/memory/suggestions/{{sid}}/edit",
        server._wrap(server._handle_edit_suggestion),
    )
    app.router.add_post(
        f"{prefix}/memory/suggestions/{{sid}}/reject",
        server._wrap(server._handle_reject_suggestion),
    )

HANDLERS = ('_suggestions_disabled_response', '_serialize_suggestion', '_handle_list_suggestions', '_handle_get_suggestion_sources', '_read_json_body', '_audit_suggestion', '_safe_sync_suggestion_memory_item', '_handle_accept_suggestion', '_handle_edit_suggestion', '_handle_reject_suggestion',)

def _suggestions_disabled_response(self) -> web.Response | None:
    """``suggestion_store`` 미주입 환경에서 503 응답을 만든다.

    accept/edit 는 ``suggestion_writer`` 도, reject 는 ``blocklist_store`` 도
    함께 필요한데 그 디테일은 각 핸들러가 별도로 검사한다. 본 헬퍼는 모든
    엔드포인트가 공통으로 거치는 1차 가드.
    """
    if self._suggestion_store is None:
        return _json_error(
            503,
            "Suggestion queue is not configured on this server",
        )
    return None

def _serialize_suggestion(self, s) -> dict:
    """``SuggestionMeta`` 를 JSON 응답 dict 로 변환."""
    return {
        "id": s.id,
        "topic": s.topic,
        "text": s.text,
        "edited_text": s.edited_text,
        "applied_text": s.applied_text,
        "confidence": s.confidence,
        "evidence_count": s.evidence_count,
        "source_msg_ids": list(s.source_msg_ids),
        "start_msg_id": s.start_msg_id,
        "end_msg_id": s.end_msg_id,
        "status": s.status,
        "reject_reason": s.reject_reason,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }

async def _handle_list_suggestions(
    self, request: web.Request
) -> web.Response:
    """``GET /admin/v1/memory/suggestions``.

    Query params:
    - ``status``: ``pending`` (default) / ``all`` / ``accepted`` / ``edited`` / ``rejected``.
      ``all`` 은 디버깅/감사 용도로 history 전체를 노출한다.

    Response:
    ``{"suggestions": [...], "total": N, "pending_count": M, "auto_promote": {...}}``
    """
    guard = self._suggestions_disabled_response()
    if guard is not None:
        return guard

    status_filter = (request.query.get("status") or "pending").strip().lower()
    all_items = self._suggestion_store.load()
    if status_filter == "all":
        items = list(all_items)
    else:
        items = [s for s in all_items if s.status == status_filter]
    items.sort(key=lambda s: s.updated_at, reverse=True)

    return _json_ok({
        "suggestions": [self._serialize_suggestion(s) for s in items],
        "total": len(items),
        "pending_count": sum(1 for s in all_items if s.status == "pending"),
    })

async def _handle_get_suggestion_sources(
    self, request: web.Request
) -> web.Response:
    """``GET /admin/v1/memory/suggestions/{sid}/sources``.

    BIZ-79 DoD §3 — UI 의 "근거 메시지 보기" 액션이 호출하는 엔드포인트.
    ``ConversationStore`` 미주입이면 503, suggestion 미존재면 404.
    """
    guard = self._suggestions_disabled_response()
    if guard is not None:
        return guard
    if self._conversation_store is None:
        return _json_error(
            503, "Source linkage is not configured on this server"
        )

    sid = (request.match_info.get("sid") or "").strip()
    s = self._suggestion_store.get(sid)
    if s is None:
        return _json_error(404, f"Suggestion not found: {sid}")

    rows = self._conversation_store.get_messages_by_ids(s.source_msg_ids)
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
        "suggestion": self._serialize_suggestion(s),
        "sources": sources,
    })

async def _read_json_body(request: web.Request) -> dict:
    """JSON 본문을 안전하게 dict 로 읽는다 (없거나 잘못되면 빈 dict)."""
    try:
        raw = await request.read()
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}

def _audit_suggestion(
    self,
    request: web.Request,
    action: str,
    suggestion,
    details: dict | None = None,
) -> None:
    """Suggestion mutation 을 감사 로그에 기록한다.

    operator review action 은 USER.md 변경/블록리스트 추가로 이어지므로
    ``rotate``/``patch_config`` 와 동일한 수준의 감사 추적이 필요하다.
    """
    try:
        after: dict = {
            "id": suggestion.id,
            "topic": suggestion.topic,
            "status": suggestion.status,
        }
        if details:
            after.update(details)
        self._audit.append(
            action=action,
            area="memory",
            target=f"suggestion:{suggestion.id}",
            before={"topic": suggestion.topic, "text": suggestion.text},
            after=after,
            actor_id=_actor_from(request),
            undoable=False,  # USER.md append/blocklist add 는 단방향 액션
        )
    except Exception:  # noqa: BLE001 — 감사 실패가 핸들러 응답을 막지 않도록.
        logger.exception("Failed to write suggestion audit entry")

def _safe_sync_suggestion_memory_item(self, suggestion) -> None:
    """Keep reviewed USER insights in memory_items without breaking Admin API flow."""
    if self._conversation_store is None:
        return
    try:
        sync_suggestion_to_memory_item(self._conversation_store, suggestion)
    except Exception:  # noqa: BLE001
        logger.exception(
            "memory_items sync failed for suggestion %s; continuing",
            getattr(suggestion, "id", ""),
        )

async def _handle_accept_suggestion(
    self, request: web.Request
) -> web.Response:
    """``POST /admin/v1/memory/suggestions/{sid}/accept`` — 원문 그대로 적용."""
    guard = self._suggestions_disabled_response()
    if guard is not None:
        return guard
    if self._suggestion_writer is None:
        return _json_error(
            503,
            "USER.md writer is not configured — cannot apply suggestions",
        )

    sid = (request.match_info.get("sid") or "").strip()
    s = self._suggestion_store.get(sid)
    if s is None:
        return _json_error(404, f"Suggestion not found: {sid}")
    if s.status in TERMINAL_STATUSES:
        return _json_error(
            409,
            f"Suggestion already in terminal state: {s.status}",
        )

    try:
        self._suggestion_writer(s.text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to write accepted suggestion")
        return _json_error(500, f"Failed to apply suggestion: {exc}")

    updated = self._suggestion_store.update_status(sid, "accepted")
    # update_status 의 반환값이 None 이라면 race — 그래도 writer 가 이미 USER.md
    # 를 갱신했으므로 200 으로 응답하고 클라이언트에 최선 정보 제공.
    result = updated or s
    self._safe_sync_suggestion_memory_item(result)
    self._audit_suggestion(request, "accept_suggestion", result)
    return _json_ok(self._serialize_suggestion(result))

async def _handle_edit_suggestion(
    self, request: web.Request
) -> web.Response:
    """``POST /admin/v1/memory/suggestions/{sid}/edit`` — body.text 로 치환 후 적용."""
    guard = self._suggestions_disabled_response()
    if guard is not None:
        return guard
    if self._suggestion_writer is None:
        return _json_error(
            503,
            "USER.md writer is not configured — cannot apply suggestions",
        )

    sid = (request.match_info.get("sid") or "").strip()
    body = await self._read_json_body(request)
    edited_text = (body.get("text") or "").strip()
    if not edited_text:
        return _json_error(422, "Body must include non-empty 'text' field")

    s = self._suggestion_store.get(sid)
    if s is None:
        return _json_error(404, f"Suggestion not found: {sid}")
    if s.status in TERMINAL_STATUSES:
        return _json_error(
            409,
            f"Suggestion already in terminal state: {s.status}",
        )

    try:
        self._suggestion_writer(edited_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to write edited suggestion")
        return _json_error(500, f"Failed to apply suggestion: {exc}")

    updated = self._suggestion_store.update_status(
        sid, "edited", edited_text=edited_text
    )
    result = updated or s
    self._safe_sync_suggestion_memory_item(result)
    self._audit_suggestion(
        request,
        "edit_suggestion",
        result,
        details={"edited_text": edited_text},
    )
    return _json_ok(self._serialize_suggestion(result))

async def _handle_reject_suggestion(
    self, request: web.Request
) -> web.Response:
    """``POST /admin/v1/memory/suggestions/{sid}/reject`` — 블록리스트 추가."""
    guard = self._suggestions_disabled_response()
    if guard is not None:
        return guard
    if self._blocklist_store is None:
        return _json_error(
            503,
            "Blocklist store is not configured — cannot block topics",
        )

    sid = (request.match_info.get("sid") or "").strip()
    body = await self._read_json_body(request)
    reason = (body.get("reason") or "").strip() or None

    # blocklist 차단 기간 — 30/90/180일 또는 null(영구).
    # BIZ-93: 운영자가 모달에서 단일 선택. None 또는 명시적 null 은 영구.
    # 그 외 값은 422 로 반려해 잘못된 클라이언트 입력을 즉시 가시화한다.
    period_raw = body.get("blocklist_period_days", None)
    ttl_seconds: int | None = None
    if period_raw is not None:
        try:
            period_days = int(period_raw)
        except (TypeError, ValueError):
            return _json_error(
                422,
                "blocklist_period_days must be one of 30, 90, 180, or null",
            )
        if period_days not in (30, 90, 180):
            return _json_error(
                422,
                "blocklist_period_days must be one of 30, 90, 180, or null",
            )
        ttl_seconds = period_days * 86400

    s = self._suggestion_store.get(sid)
    if s is None:
        return _json_error(404, f"Suggestion not found: {sid}")
    if s.status in TERMINAL_STATUSES:
        return _json_error(
            409,
            f"Suggestion already in terminal state: {s.status}",
        )

    # 1) 블록리스트 추가 — 다음 dreaming 사이클부터 같은 topic 은 필터링됨.
    #    ttl_seconds=None 은 영구 차단, 양수는 해당 시간 후 만료.
    # 2) suggestion 행 status 를 rejected 로 마킹 (UI 에서 사라짐, audit 보존).
    self._blocklist_store.add(
        s.topic, ttl_seconds=ttl_seconds, reason=reason
    )
    updated = self._suggestion_store.update_status(
        sid, "rejected", reject_reason=reason
    )
    result = updated or s
    self._safe_sync_suggestion_memory_item(result)
    self._audit_suggestion(
        request,
        "reject_suggestion",
        result,
        details={
            "reason": reason or "",
            "blocklist_period_days": (
                int(period_raw) if ttl_seconds is not None else None
            ),
        },
    )
    return _json_ok(self._serialize_suggestion(result))
