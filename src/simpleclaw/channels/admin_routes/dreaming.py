"""Admin API dreaming route handlers."""

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
    """Dreaming run/status observability routes를 등록한다."""
    app.router.add_get(
        f"{prefix}/memory/dreaming/runs",
        server._wrap(server._handle_list_dreaming_runs),
    )
    app.router.add_get(
        f"{prefix}/memory/dreaming/status",
        server._wrap(server._handle_dreaming_status),
    )

HANDLERS = ('_serialize_dreaming_run', '_suggestion_rejection_rate', '_handle_list_dreaming_runs', '_handle_dreaming_status',)

def _serialize_dreaming_run(self, rec) -> dict:
    """``DreamingRunRecord`` 를 JSON 응답 dict 로 변환.

    UI 가 한 행에서 status/duration 을 즉시 표시할 수 있도록 파생 필드도 포함한다.
    """
    return {
        "id": rec.id,
        "started_at": rec.started_at.isoformat(),
        "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
        "duration_seconds": rec.duration_seconds,
        "input_msg_count": rec.input_msg_count,
        "generated_insight_count": rec.generated_insight_count,
        "rejected_count": rec.rejected_count,
        "error": rec.error,
        "skip_reason": rec.skip_reason,
        "status": rec.status,
        "details": rec.details or {},
    }

def _suggestion_rejection_rate(self) -> dict:
    """``SuggestionStore`` 에 누적된 운영자 review 결과로부터 거절률 계산.

    DoD 의 "거절률" KPI 는 dreaming 결과(=suggestion) 에 대한 운영자 리뷰 신호이다
    (BIZ-66 §3-K: "거절률 KPI는 H의 Admin Review Loop 신호에서 산출"). 따라서
    suggestion_store 가 비활성이면 ``None`` 을 반환해 UI 가 "측정 불가" 로 표시.

    Returns:
        ``{"reviewed": int, "rejected": int, "rate": float|None}``.
        ``reviewed`` 가 0 이면 ``rate`` 는 ``None`` (분모 0).
    """
    if self._suggestion_store is None:
        return {"reviewed": 0, "rejected": 0, "rate": None}
    reviewed = 0
    rejected = 0
    for s in self._suggestion_store.load():
        if s.status in TERMINAL_STATUSES:
            reviewed += 1
            if s.status == "rejected":
                rejected += 1
    rate = (rejected / reviewed) if reviewed > 0 else None
    return {"reviewed": reviewed, "rejected": rejected, "rate": rate}

async def _handle_list_dreaming_runs(
    self, request: web.Request
) -> web.Response:
    """``GET /admin/v1/memory/dreaming/runs?limit=N``.

    최근 N건의 사이클 메트릭을 최신순으로 반환. ``limit`` 기본 20, 상한 200
    (sidecar 자체가 200건 정도만 보존).

    Response: ``{"runs": [...], "total": N}``.
    503 if ``dreaming_run_store`` 미주입.
    """
    if self._dreaming_run_store is None:
        return _json_error(
            503, "Dreaming run metrics are not configured on this server"
        )
    # limit 파라미터 — 정수가 아니면 기본값으로 폴백(검색바 오타 방어).
    try:
        limit = int(request.query.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(200, limit))

    rows = self._dreaming_run_store.list_recent(limit=limit)
    return _json_ok({
        "runs": [self._serialize_dreaming_run(r) for r in rows],
        "total": len(rows),
    })

async def _handle_dreaming_status(
    self, request: web.Request
) -> web.Response:
    """``GET /admin/v1/memory/dreaming/status``.

    Memory 화면의 KPI 패널이 단일 호출로 받아갈 수 있도록 다음을 합쳐 반환:
    - ``last_run`` / ``last_successful_run``: 가장 최근 회차/성공 회차.
    - ``next_run``: 데몬에서 추정한 다음 실행 예정 시각(string ISO|None).
    - ``trigger`` 진단: overnight_hour, idle_threshold, blockers 등 오늘 트리거가
      왜 (아직) 안 돌았는지 사람이 읽을 수 있는 메시지.
    - ``kpi_7d``: 7일 윈도우 집계(success/skip/error 카운트, msg/insight totals,
      skip_breakdown).
    - ``rejection``: 운영자 리뷰 누적 거절률 (suggestion_store 에서 계산).

    ``dreaming_run_store`` 가 없으면 KPI 와 last_run 을 None 으로 비우되 응답
    자체는 200 으로 돌려준다 — UI 가 "메트릭 비활성" 안내를 그릴 수 있게.
    """
    last_run = None
    last_successful = None
    kpi_7d: dict | None = None
    if self._dreaming_run_store is not None:
        lr = self._dreaming_run_store.last_run()
        ls = self._dreaming_run_store.last_successful_run()
        if lr is not None:
            last_run = self._serialize_dreaming_run(lr)
        if ls is not None:
            last_successful = self._serialize_dreaming_run(ls)
        kpi_7d = self._dreaming_run_store.kpi_window(days=7)

    # 데몬에서 만든 status 컨텍스트 — 미주입 시 빈 dict 로 폴백(엔드포인트는 동작).
    provider_state: dict = {}
    if self._dreaming_status_provider is not None:
        try:
            provider_state = dict(self._dreaming_status_provider() or {})
        except Exception:
            # provider 실패는 KPI 응답을 막지 않는다 — 진단 가시성이 핵심.
            logger.exception("dreaming_status_provider raised; returning empty state")
            provider_state = {}

    return _json_ok({
        "last_run": last_run,
        "last_successful_run": last_successful,
        "next_run": provider_state.get("next_run"),
        "overnight_hour": provider_state.get("overnight_hour"),
        "idle_threshold_seconds": provider_state.get("idle_threshold_seconds"),
        "trigger_blockers": list(provider_state.get("trigger_blockers") or []),
        "trigger_message": provider_state.get("trigger_message"),
        "kpi_7d": kpi_7d,
        "rejection": self._suggestion_rejection_rate(),
        "metrics_enabled": self._dreaming_run_store is not None,
    })
