"""Admin API secrets route handlers."""

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
    """Secret 조회/reveal/rotate routes를 등록한다."""
    app.router.add_get(f"{prefix}/secrets", server._wrap(server._handle_list_secrets))
    app.router.add_post(
        f"{prefix}/secrets/master/rotate",
        server._wrap(server._handle_rotate_master_secret),
    )
    app.router.add_post(
        f"{prefix}/secrets/{{name}}/reveal", server._wrap(server._handle_reveal_secret)
    )
    app.router.add_post(
        f"{prefix}/secrets/{{name}}/rotate", server._wrap(server._handle_rotate_secret)
    )

HANDLERS = ('_handle_list_secrets', '_handle_reveal_secret', '_handle_rotate_secret', '_handle_rotate_master_secret', '_lookup_secret', '_gc_nonces',)

async def _handle_list_secrets(self, request: web.Request) -> web.Response:
    """백엔드별로 등록된 시크릿 메타데이터 — 이름·마지막 회전 시각만."""
    items: list[dict] = []
    for backend_name in _BACKEND_LABELS:
        try:
            backend = self._secrets.get_backend(backend_name)
        except SecretsError:
            continue
        try:
            names = _list_backend_keys(backend)
        except SecretsError as exc:
            logger.warning(
                "백엔드 %s 키 목록 조회 실패: %s", backend_name, exc
            )
            names = []
        for name in names:
            items.append(
                {
                    "name": name,
                    "backend": backend_name,
                    "last_rotated_at": _last_rotated_for(self._audit, name),
                }
            )
    return _json_ok({"secrets": items})

async def _handle_reveal_secret(self, request: web.Request) -> web.Response:
    """시크릿 평문을 일회성 nonce와 함께 반환 — 15초 TTL."""
    name = request.match_info["name"]
    backend_name = request.query.get("backend", "")
    backend, value = self._lookup_secret(name, backend_name)
    if value is None:
        return _json_error(404, f"Secret not found: {name}")

    nonce = secrets.token_urlsafe(24)
    self._gc_nonces()
    self._reveal_nonces[nonce] = _RevealEntry(
        name=name,
        backend=backend,
        expires_at=time.monotonic() + self._reveal_ttl_seconds,
    )
    self._metrics.secret_reveals += 1
    self._audit.append(
        action="secret.reveal",
        area="secrets",
        target=f"{backend}:{name}",
        before=None,
        after=None,
        outcome="applied",
        requires_restart=False,
        undoable=False,
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )
    return _json_ok(
        {
            "name": name,
            "backend": backend,
            "value": value,
            "nonce": nonce,
            "expires_in_seconds": int(self._reveal_ttl_seconds),
        }
    )

async def _handle_rotate_secret(self, request: web.Request) -> web.Response:
    """시크릿을 새 값으로 회전 — 본문 ``{"value": "...", "backend": "..."}``."""
    name = request.match_info["name"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_error(400, "Invalid JSON payload")
    if not isinstance(body, dict) or "value" not in body:
        return _json_error(400, "Missing field: value")
    backend = str(body.get("backend") or _detect_backend(self._secrets, name) or "keyring")
    value = body["value"]
    if not isinstance(value, str) or not value:
        return _json_error(400, "value must be non-empty string")

    try:
        self._secrets.store(backend, name, value)
    except SecretsError as exc:
        return _json_error(400, str(exc))

    # BIZ-245 — 회전 직후 외부 sink 동기화 (예: ``web/admin/.env.local`` 의
    # ``ADMIN_API_TOKEN``). 회전은 이미 백엔드에 반영됐으므로 콜백 실패는 ERROR 로그만
    # 남기고 응답에는 영향을 주지 않는다 — 운영자가 401 보다 더 큰 장애(rotate 실패)
    # 로 떨어지는 것을 피한다.
    if self._secret_rotation_cb is not None:
        try:
            self._secret_rotation_cb(backend, name, value)
        except Exception as exc:  # noqa: BLE001 — 콜백 임의 구현
            logger.warning(
                "시크릿 회전 후 동기화 콜백 실패 (%s:%s): %s",
                backend,
                name,
                exc,
            )

    self._metrics.secret_rotations += 1
    # 시크릿 회전의 ``after``는 평문 자체이므로 키 이름과 무관하게 강제 마스킹.
    # ``_mask_secrets``는 키 이름 기반이라 ``value`` 같은 평범한 키를 잡지 못하므로,
    # 여기서 회전 의미를 알고 있는 핸들러가 마스킹을 책임진다.
    from simpleclaw.channels.admin_audit import _mask_value

    entry = self._audit.append(
        action="secret.rotate",
        area="secrets",
        target=f"{backend}:{name}",
        before=None,
        after={"value": _mask_value(value)},
        outcome="applied",
        requires_restart=False,
        undoable=False,
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )
    return _json_ok(
        {"outcome": "applied", "audit_id": entry.id, "backend": backend, "name": name}
    )

async def _handle_rotate_master_secret(self, request: web.Request) -> web.Response:
    """마스터 키를 회전하고 모든 file 백엔드 시크릿을 재암호화한다.

    절차:
    1) 현재 마스터 키로 모든 ``file:`` 시크릿을 해독해 메모리에 보관
    2) 새 마스터 키 생성·저장 (이전 키는 ``master.key.{ts}.bak``으로 백업)
    3) 메모리 평문을 새 키로 다시 암호화해 볼트에 저장
    """
    try:
        file_backend = self._secrets.get_backend("file")
    except SecretsError as exc:
        return _json_error(400, str(exc))
    if not isinstance(file_backend, EncryptedFileBackend):
        return _json_error(400, "file backend is not an EncryptedFileBackend")

    try:
        count = _rotate_master_key(file_backend)
    except SecretsError as exc:
        return _json_error(500, f"Master key rotation failed: {exc}")

    self._metrics.master_key_rotations += 1
    entry = self._audit.append(
        action="secret.rotate_master",
        area="secrets",
        target="master_key",
        before=None,
        after={"reencrypted_count": count},
        outcome="applied",
        requires_restart=False,
        undoable=False,
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )
    return _json_ok(
        {"outcome": "applied", "reencrypted_count": count, "audit_id": entry.id}
    )

def _lookup_secret(
    self, name: str, backend_name: str
) -> tuple[str, str | None]:
    """이름과 (옵셔널) 백엔드를 받아 평문 값을 반환한다.

    백엔드를 명시하지 않으면 ``env`` → ``keyring`` → ``file`` 순으로 탐색한다.
    """
    if backend_name:
        try:
            backend = self._secrets.get_backend(backend_name)
        except SecretsError:
            return backend_name, None
        try:
            return backend_name, backend.get(name)
        except SecretsError:
            return backend_name, None

    for label in _BACKEND_LABELS:
        try:
            backend = self._secrets.get_backend(label)
        except SecretsError:
            continue
        try:
            value = backend.get(name)
        except SecretsError:
            continue
        if value is not None:
            return label, value
    return "", None

def _gc_nonces(self) -> None:
    """만료된 reveal nonce를 정리한다 — TTL 지난 항목만 제거."""
    now = time.monotonic()
    expired = [n for n, e in self._reveal_nonces.items() if e.expires_at < now]
    for n in expired:
        self._reveal_nonces.pop(n, None)
