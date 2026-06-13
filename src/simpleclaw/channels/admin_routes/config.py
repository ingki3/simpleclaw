"""Admin API config route handlers."""

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
    """Config read/patch routes를 등록한다."""
    app.router.add_get(f"{prefix}/config", server._wrap(server._handle_get_config_all))
    app.router.add_get(
        f"{prefix}/config/{{area}}", server._wrap(server._handle_get_config_area)
    )
    app.router.add_patch(
        f"{prefix}/config/{{area}}", server._wrap(server._handle_patch_config_area)
    )

HANDLERS = ('_read_yaml', '_write_yaml', '_prune_backups', '_handle_get_config_all', '_handle_get_config_area', '_extract_area', '_handle_patch_config_area', '_merge_patch_into_full',)

def _read_yaml(self) -> dict:
    """현재 ``config.yaml``을 읽어 dict로 반환한다 (없으면 빈 dict)."""
    if not self._config_path.is_file():
        return {}
    try:
        with open(self._config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("config.yaml 읽기 실패: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}

def _write_yaml(self, data: dict) -> None:
    """``config.yaml``을 atomic하게 다시 쓴다 (백업 ``config.yaml.{ts}.bak``).

    admin-requirements §4.3 — 편집 시 백업 자동 생성, 최근 10개 보존.
    """
    self._config_path.parent.mkdir(parents=True, exist_ok=True)

    # 백업 — 기존 파일이 있으면 ts 붙여서 보존.
    if self._config_path.is_file():
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = self._config_path.with_suffix(
            self._config_path.suffix + f".{ts}.bak"
        )
        try:
            bak.write_bytes(self._config_path.read_bytes())
        except OSError:
            pass
        self._prune_backups()

    tmp = self._config_path.with_suffix(self._config_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    tmp.replace(self._config_path)

def _prune_backups(self, keep: int = 10) -> None:
    """오래된 ``config.yaml.{ts}.bak`` 파일을 keep개만 남기고 정리."""
    parent = self._config_path.parent
    if not parent.is_dir():
        return
    baks = sorted(parent.glob(f"{self._config_path.name}.*.bak"))
    excess = len(baks) - keep
    if excess <= 0:
        return
    for old in baks[:excess]:
        try:
            old.unlink()
        except OSError:
            pass

async def _handle_get_config_all(self, request: web.Request) -> web.Response:
    """전체 머지된 설정 — 시크릿은 마스킹된 ref/문자열 형태로 노출."""
    data = self._read_yaml()
    return _json_ok({"config": _mask_for_response(data)})

async def _handle_get_config_area(self, request: web.Request) -> web.Response:
    area = request.match_info["area"]
    if area not in AREA_TO_YAML_KEY:
        return _json_error(404, f"Unknown area: {area}")
    data = self._read_yaml()
    target = self._extract_area(data, area)
    return _json_ok({"area": area, "config": _mask_for_response(target)})

def _extract_area(self, full: dict, area: str) -> Any:
    """영역 이름에 매핑된 YAML 키(들)에서 부분 트리를 추출한다."""
    mapping = AREA_TO_YAML_KEY[area]
    if isinstance(mapping, list):
        return {key: full.get(key, {}) for key in mapping}
    return _get_dotted(full, mapping) or {}

async def _handle_patch_config_area(self, request: web.Request) -> web.Response:
    area = request.match_info["area"]
    if area not in AREA_TO_YAML_KEY:
        return _json_error(404, f"Unknown area: {area}")

    try:
        patch = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_error(400, "Invalid JSON payload")
    if not isinstance(patch, dict):
        return _json_error(400, "Patch must be a JSON object")

    # 1) 검증 — 422 fast-fail.
    errors = validate_patch(area, patch)
    if errors:
        self._metrics.rejected += 1
        self._audit.append(
            action="config.update",
            area=area,
            target=area,
            before=None,
            after=patch,
            outcome="rejected",
            requires_restart=False,
            undoable=False,
            reason="; ".join(errors),
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_error(422, "Validation failed", details={"errors": errors})

    # 2) 정책 분석 — Hot/Service-restart/Process-restart.
    policy = classify_keys(area, patch)

    # 3) before 스냅샷 + diff 계산.
    full = self._read_yaml()
    before_snap = _project(full, area, patch)

    # 4) dry-run 처리 — 파일/볼트 미수정.
    dry_run = _truthy_query(request, "dry_run")
    if dry_run:
        self._metrics.config_dry_runs += 1
        self._audit.append(
            action="config.update",
            area=area,
            target=area,
            before=before_snap,
            after=patch,
            outcome="dry_run",
            requires_restart=policy.requires_restart,
            affected_modules=policy.affected_modules,
            undoable=False,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok(
            {
                "outcome": "dry_run",
                "diff": {
                    "before": _mask_for_response(before_snap),
                    "after": _mask_for_response(patch),
                },
                "policy": _policy_to_dict(policy),
            }
        )

    # 5) Process-restart는 즉시 반영 X — 펜딩 적재.
    if policy.level == PROCESS_RESTART:
        pending_path = _pending_changes_path(self._state_dir)
        pending = _load_pending(pending_path)
        self._merge_patch_into_full(pending, area, patch)
        _save_pending(pending_path, pending)

        self._metrics.pending_changes += 1
        entry = self._audit.append(
            action="config.update",
            area=area,
            target=area,
            before=before_snap,
            after=patch,
            outcome="pending",
            requires_restart=True,
            affected_modules=policy.affected_modules,
            undoable=True,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok(
            {
                "outcome": "pending",
                "audit_id": entry.id,
                "policy": _policy_to_dict(policy),
                "message": "데몬 재시작 후 적용됩니다.",
            }
        )

    # 6) Hot / Service-restart — yaml 즉시 반영.
    self._merge_patch_into_full(full, area, patch)
    self._write_yaml(full)
    self._metrics.config_patches += 1

    # Hot이면 reload 콜백 호출 — 등록되지 않았으면 lazy loader가 처리.
    if policy.level == HOT and self._reload_cb is not None:
        try:
            result = self._reload_cb(area, patch)
            if hasattr(result, "__await__"):
                await result  # type: ignore[func-returns-value]
        except Exception:  # noqa: BLE001
            logger.exception("reload callback failed for area=%s", area)

    entry = self._audit.append(
        action="config.update",
        area=area,
        target=area,
        before=before_snap,
        after=patch,
        outcome="applied",
        requires_restart=policy.requires_restart,
        affected_modules=policy.affected_modules,
        undoable=True,
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )

    return _json_ok(
        {
            "outcome": "applied",
            "audit_id": entry.id,
            "policy": _policy_to_dict(policy),
        }
    )

def _merge_patch_into_full(
    self, full: dict, area: str, patch: dict
) -> None:
    """``area`` 매핑을 따라 ``full`` 트리에 ``patch``를 깊은 머지한다.

    ``channels`` 같은 그룹 별칭은 patch의 최상위 키별로 분기하고,
    ``daemon.cron_retry`` 같은 dotted 매핑은 해당 위치를 정확히 가리킨다.
    """
    mapping = AREA_TO_YAML_KEY[area]
    if isinstance(mapping, list):
        for key in mapping:
            if key in patch and isinstance(patch[key], dict):
                sub = full.setdefault(key, {})
                if not isinstance(sub, dict):
                    sub = {}
                    full[key] = sub
                _deep_merge(sub, patch[key])
            elif key in patch:
                full[key] = copy.deepcopy(patch[key])
        return

    # dotted path 매핑
    if "." in mapping:
        existing = _get_dotted(full, mapping)
        if not isinstance(existing, dict):
            existing = {}
        _deep_merge(existing, patch)
        _set_dotted(full, mapping, existing)
        return

    # 단일 최상위 키
    sub = full.setdefault(mapping, {})
    if not isinstance(sub, dict):
        sub = {}
        full[mapping] = sub
    _deep_merge(sub, patch)
