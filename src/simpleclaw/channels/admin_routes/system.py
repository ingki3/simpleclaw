"""Admin API system route handlers."""

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
    """Audit/log/health/system/channel test routes를 등록한다."""
    app.router.add_get(f"{prefix}/audit", server._wrap(server._handle_search_audit))
    app.router.add_post(
        f"{prefix}/audit/{{id}}/undo", server._wrap(server._handle_undo_audit)
    )
    app.router.add_get(f"{prefix}/logs", server._wrap(server._handle_search_logs))
    app.router.add_get(f"{prefix}/health", server._wrap(server._handle_health))
    app.router.add_get(
        f"{prefix}/system/info", server._wrap(server._handle_system_info)
    )
    app.router.add_post(
        f"{prefix}/system/restart", server._wrap(server._handle_system_restart)
    )
    app.router.add_post(
        f"{prefix}/channels/{{name}}/test",
        server._wrap(server._handle_test_channel),
    )

HANDLERS = ('_handle_search_audit', '_handle_undo_audit', '_handle_search_logs', '_handle_health', '_handle_system_info', '_handle_system_restart', '_handle_test_channel', '_default_channel_test',)

async def _handle_search_audit(self, request: web.Request) -> web.Response:
    q = request.query
    try:
        limit = int(q.get("limit", "200"))
    except ValueError:
        limit = 200
    entries = self._audit.search(
        since=q.get("since"),
        actor=q.get("actor"),
        area=q.get("area"),
        outcome=q.get("outcome"),
        action=q.get("action"),
        limit=limit,
    )
    return _json_ok(
        {"entries": [_audit_to_dict(e) for e in entries]}
    )

async def _handle_undo_audit(self, request: web.Request) -> web.Response:
    entry_id = request.match_info["id"]
    target = self._audit.get(entry_id)
    if target is None:
        return _json_error(404, f"Audit entry not found: {entry_id}")
    if not target.undoable:
        return _json_error(409, "Entry is not undoable")
    if target.action != "config.update":
        return _json_error(409, "Only config.update entries are undoable")
    if target.outcome not in ("applied", "pending"):
        return _json_error(409, f"Cannot undo outcome={target.outcome}")
    if not isinstance(target.before, dict):
        return _json_error(409, "Audit entry has no restorable 'before' snapshot")

    # before를 새 PATCH로 적용 — 결과는 새 audit entry로 기록(이력 보존).
    full = self._read_yaml()
    self._merge_patch_into_full(full, target.area, target.before)
    self._write_yaml(full)

    # 펜딩 항목이라면 펜딩 파일에서도 제거 — 정확한 삭제는 어려우니
    # 단순히 같은 패치 트리를 펜딩에서 빼낸다.
    pending_path = _pending_changes_path(self._state_dir)
    pending = _load_pending(pending_path)
    if pending:
        try:
            self._merge_patch_into_full(pending, target.area, target.before)
            _save_pending(pending_path, pending)
        except Exception:
            pass

    if self._reload_cb is not None:
        try:
            result = self._reload_cb(target.area, target.before)
            if hasattr(result, "__await__"):
                await result  # type: ignore[func-returns-value]
        except Exception:
            logger.exception("reload callback failed during undo")

    self._metrics.audit_undos += 1
    new_entry = self._audit.append(
        action="config.update",
        area=target.area,
        target=target.target,
        before=target.after,  # 의미상 현재값 → 이전값으로 되돌림
        after=target.before,
        outcome="applied",
        requires_restart=False,
        undoable=True,
        reason=f"undo of {entry_id}",
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )
    return _json_ok({"outcome": "applied", "audit_id": new_entry.id})

async def _handle_search_logs(self, request: web.Request) -> web.Response:
    """주입된 ``StructuredLogger``로부터 로그 항목을 조회한다."""
    slog = self._structured
    if slog is None:
        return _json_ok({"entries": []})
    get_entries = getattr(slog, "get_entries", None)
    if not callable(get_entries):
        return _json_ok({"entries": []})

    q = request.query
    kwargs: dict[str, Any] = {}
    if "trace_id" in q:
        kwargs["trace_id"] = q["trace_id"]
    if "limit" in q:
        try:
            kwargs["limit"] = int(q["limit"])
        except ValueError:
            kwargs["limit"] = 100
    try:
        entries = get_entries(**kwargs)
    except TypeError:
        # 시그니처가 다른 로거가 주입된 경우의 안전 폴백.
        entries = get_entries()

    # 추가 필터(level/module 등)는 응답 측에서 슬라이싱.
    level = q.get("level")
    module = q.get("module")
    out = []
    for e in entries:
        data = _entry_to_dict(e)
        if level and data.get("level") != level:
            continue
        if module and module not in (data.get("action_type") or ""):
            continue
        out.append(data)
    return _json_ok({"entries": out})

async def _handle_health(self, request: web.Request) -> web.Response:
    snapshot: dict = {
        "status": "ok",
        "uptime_seconds": int(time.time() - self._started_at)
        if self._started_at
        else 0,
        "metrics": self._metrics.__dict__,
        "pending_changes": bool(_load_pending(_pending_changes_path(self._state_dir))),
    }
    if self._health_provider is not None:
        try:
            extra = self._health_provider() or {}
            if isinstance(extra, dict):
                snapshot.update(extra)
        except Exception:
            logger.exception("health_provider failed")
    # BIZ-442 — drain 상태/active operation 수. deploy script 의 quiesce 폴링과
    # 운영자 점검이 이 키 하나로 "지금 재시작해도 되는가"를 판단한다.
    if self._drain_status_provider is not None:
        try:
            drain_status = self._drain_status_provider()
            if isinstance(drain_status, dict):
                snapshot["drain"] = drain_status
        except Exception:
            logger.exception("drain_status_provider failed")
    return _json_ok(snapshot)

async def _handle_system_info(self, request: web.Request) -> web.Response:
    """진단 정보 — 버전·PID·uptime·DB 경로·디스크 사용량을 반환한다.

    UI(System 화면) 좌측 카드의 정적 데이터원이며, 헬스 폴링과 분리해 1회만
    조회한다. 외부 부수효과가 없는 read-only 핸들러로 별도 감사 로그를
    남기지 않는다.
    """
    # 버전 정보 — pyproject.toml의 단일 소스를 importlib.metadata로 조회.
    version = "unknown"
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        try:
            version = _pkg_version("simpleclaw")
        except PackageNotFoundError:
            version = "unknown"
    except Exception:
        pass

    # 빌드 해시 — 환경변수(SIMPLECLAW_BUILD_SHA)가 있으면 사용. 운영자가
    # 명시 주입하지 않으면 None으로 둔다(수동 git 호출은 의도적으로 회피).
    build_sha = os.environ.get("SIMPLECLAW_BUILD_SHA") or None

    # config.yaml에서 daemon.db_path를 우선 채택 — 없으면 admin_state_dir의
    # 형제 conversations.db를 폴백으로 노출(파일 존재 여부도 함께 응답).
    # BIZ-313: 런타임 디렉터리 기본 폴백은
    # ``~/.simpleclaw-agent/default/conversations.db`` 로 변경.
    cfg = self._read_yaml()
    db_path_str = (
        _get_dotted(cfg, "agent.db_path")
        or _get_dotted(cfg, "daemon.db_path")
        or "~/.simpleclaw-agent/default/conversations.db"
    )
    db_path = Path(str(db_path_str)).expanduser()
    db_size = None
    db_exists = db_path.is_file()
    if db_exists:
        try:
            db_size = db_path.stat().st_size
        except OSError:
            db_size = None

    # 디스크 사용량 — config.yaml이 있는 디렉토리(워크스페이스 루트로 간주)를
    # 기준으로 한 번만 측정. 컨테이너/원격 마운트에서는 데몬 위치가 더 의미 있다.
    disk = None
    try:
        target = self._config_path.parent if self._config_path.parent.exists() else Path.cwd()
        usage = shutil.disk_usage(target)
        disk = {
            "path": str(target),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }
    except OSError:
        disk = None

    snapshot: dict[str, Any] = {
        "version": version,
        "build_sha": build_sha,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "pid": os.getpid(),
        "uptime_seconds": int(time.time() - self._started_at)
        if self._started_at
        else 0,
        "config_path": str(self._config_path),
        "db_path": str(db_path),
        "db_exists": db_exists,
        "db_size_bytes": db_size,
        "disk": disk,
        "host": self._host,
        "port": self._port,
    }
    return _json_ok(snapshot)

async def _handle_system_restart(self, request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    if not isinstance(body, dict):
        body = {}

    # 펜딩 변경을 yaml에 머지 — 데몬이 재기동하면서 새 값을 읽도록.
    pending_path = _pending_changes_path(self._state_dir)
    pending = _load_pending(pending_path)
    merged_count = 0
    if pending:
        full = self._read_yaml()
        _deep_merge(full, pending)
        self._write_yaml(full)
        try:
            pending_path.unlink()
        except OSError:
            pass
        merged_count = sum(1 for _ in _flatten_keys(pending))

    entry = self._audit.append(
        action="system.restart",
        area="system",
        target="daemon",
        before=None,
        after={"reason": body.get("reason", ""), "applied_pending": merged_count},
        outcome="applied",
        requires_restart=True,
        undoable=False,
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )

    if self._restart_cb is not None:
        try:
            result = self._restart_cb(body)
            if hasattr(result, "__await__"):
                await result  # type: ignore[func-returns-value]
        except Exception:
            logger.exception("restart_callback failed")

    return _json_ok(
        {
            "outcome": "applied",
            "audit_id": entry.id,
            "applied_pending": merged_count,
        }
    )

async def _handle_test_channel(self, request: web.Request) -> web.Response:
    """채널별 테스트 메시지를 발송하고 상태 코드/지연을 반환한다.

    - 경로 ``/admin/v1/channels/{name}/test``의 ``name``은 ``telegram``/``webhook``.
    - 요청 본문(JSON, 선택): ``{"message": "...", "target": "..."}``. 미지정 시
      ``"Hello from admin"`` + 채널 기본 타깃(텔레그램은 첫 화이트리스트 user_id,
      웹훅은 ``http://{host}:{port}/webhook``).
    - 응답: ``{ok, status_code, latency_ms, target?, error?}``.

    ``channel_test_callback``이 주입돼 있으면 위임하고, 그렇지 않으면 내장
    구현이 aiohttp.ClientSession으로 실제 호출을 수행한다 — 단위 테스트는
    콜백을 mock으로 주입해 외부 네트워크 의존을 끊는다.
    """
    name = request.match_info["name"]
    if name not in ("telegram", "webhook"):
        return _json_error(404, f"Unknown channel: {name}")

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    if not isinstance(body, dict):
        body = {}
    message = (
        body.get("message")
        if isinstance(body.get("message"), str) and body.get("message")
        else "Hello from admin"
    )
    target_override = body.get("target")
    options: dict = {"message": message}
    if target_override is not None:
        options["target"] = target_override

    # 콜백 우선 — 호출자가 실제 송신 메커니즘을 주입한 경우.
    try:
        if self._channel_test_cb is not None:
            raw = self._channel_test_cb(name, options)
            if hasattr(raw, "__await__"):
                result = await raw  # type: ignore[func-returns-value]
            else:
                result = raw  # type: ignore[assignment]
        else:
            result = await self._default_channel_test(name, options)
    except Exception as exc:
        logger.exception("channel test failed: name=%s", name)
        result = {
            "ok": False,
            "status_code": 0,
            "latency_ms": 0,
            "error": f"테스트 발송 중 예외: {exc}",
        }

    if not isinstance(result, dict):
        result = {"ok": False, "status_code": 0, "latency_ms": 0, "error": "콜백 응답 형식 오류"}
    # 필수 필드 보강.
    result.setdefault("ok", False)
    result.setdefault("status_code", 0)
    result.setdefault("latency_ms", 0)

    self._metrics.channel_tests += 1
    if not result.get("ok"):
        self._metrics.channel_tests_failed += 1

    # 메시지 본문은 시크릿이 아니지만, target이 토큰을 포함할 수 있으므로
    # ``after``에는 마스킹 헬퍼를 한 번 통과시킨다.
    entry = self._audit.append(
        action="channel.test",
        area="channels",
        target=name,
        before=None,
        after=_mask_for_response(
            {
                "message": message,
                "target": result.get("target"),
                "ok": result.get("ok"),
                "status_code": result.get("status_code"),
                "latency_ms": result.get("latency_ms"),
            }
        ),
        outcome="applied" if result.get("ok") else "rejected",
        requires_restart=False,
        undoable=False,
        reason=result.get("error") or "",
        actor_id=_actor_from(request),
        trace_id=request.headers.get("X-Trace-Id", ""),
    )
    return _json_ok({**result, "audit_id": entry.id})

async def _default_channel_test(
    self, name: str, options: dict
) -> dict:
    """콜백 미주입 시의 내장 발송 구현 — aiohttp로 직접 호출한다.

    외부 네트워크에 닿으므로 격리된 단위 테스트는 ``channel_test_callback``을
    주입해 본 메서드를 우회한다.
    """

    full_cfg = self._read_yaml()
    message = options.get("message") or "Hello from admin"

    if name == "telegram":
        tg = full_cfg.get("telegram") or {}
        token_ref = tg.get("bot_token")
        token = self._secrets.resolve(token_ref) if token_ref else ""
        if not token:
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": 0,
                "error": "telegram.bot_token이 설정되지 않았어요.",
            }
        target = options.get("target")
        if target is None:
            whitelist = tg.get("whitelist") or {}
            ids = (whitelist.get("user_ids") or []) + (
                whitelist.get("chat_ids") or []
            )
            if not ids:
                return {
                    "ok": False,
                    "status_code": 0,
                    "latency_ms": 0,
                    "error": "telegram whitelist가 비어 있어 발송 대상이 없어요.",
                }
            target = ids[0]

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": target, "text": message}
        return await _http_test_send(url, payload, target=str(target))

    # webhook — 자체 수신 엔드포인트로 POST.
    wh = full_cfg.get("webhook") or {}
    host = wh.get("host", "127.0.0.1")
    port = wh.get("port", 8080)
    auth_ref = wh.get("auth_token")
    auth_token = self._secrets.resolve(auth_ref) if auth_ref else ""
    target = options.get("target") or f"http://{host}:{port}/webhook"
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    # 웹훅 페이로드 모양은 ``WebhookEvent`` 직렬화에 맞춰 최소 필드만.
    payload = {
        "action_type": "test",
        "message": message,
        "source": "admin-ui",
    }
    return await _http_test_send(
        str(target), payload, target=str(target), headers=headers
    )
