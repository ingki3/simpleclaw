"""run_bot.py에 배선된 Admin API 서버 통합 테스트 (BIZ-58).

부팅 헬퍼 ``build_admin_api_server``가 다음 시나리오에서 올바르게 동작하는지 확인:

1. 토큰이 등록돼 있으면 서버가 실제 포트에 바인딩되고 ``/admin/v1/health``가
   Bearer 토큰으로 200을 응답하며, 미인증 호출은 401을 받는다.
2. ``admin_api.token_secret``이 어디서도 해소되지 않으면 ``AdminAPIBootError``로
   부팅이 명시적으로 실패한다 — silent insecure 운용을 막기 위함.
3. ``admin_api.enabled=false``면 헬퍼가 ``None``을 반환해 봇이 포트를 바인딩하지
   않는다.
4. ``cors_origins`` 가 비어 있으면 부팅은 통과하되 경고 로그가 남는다 — BIZ-246
   에서 운영자가 stale 포트를 그대로 두고 admin UI 가 깨졌던 사고 재발 방지.

실제 ``scripts/run_bot.py``는 텔레그램·LLM·DB까지 들어 통합 테스트 격리가 어렵기
때문에, 동일 코드 경로를 공유하는 ``build_admin_api_server`` 헬퍼를 직접 호출해
부팅 시 가드를 검증한다.
"""

from __future__ import annotations

import logging
import socket

import aiohttp
import pytest
import yaml

from simpleclaw.channels.admin_api_setup import (
    AdminAPIBootError,
    build_admin_api_server,
)
from simpleclaw.channels.admin_audit import AuditLog
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger
from simpleclaw.security.secrets import (
    EnvBackend,
    SecretsManager,
    set_default_manager,
)


class _InMemoryBackend:
    """OS keyring/file 백엔드를 인메모리로 갈아끼우는 더미 — 테스트 격리용."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def list_keys(self) -> list[str]:
        return list(self._store.keys())


def _free_port() -> int:
    """OS에서 사용 가능한 임의의 TCP 포트를 받아 반환한다 — 8082 충돌 회피."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def isolated_secrets():
    """``resolve_secret`` 전역 매니저를 인메모리 백엔드로 교체한다.

    ``config.load_admin_api_config``는 ``token_secret`` 참조를 해소할 때 기본
    매니저를 사용하므로, 테스트가 OS keyring/Linux Secret Service를 건드리지
    않도록 격리한다.
    """
    keyring = _InMemoryBackend("keyring")
    file_be = _InMemoryBackend("file")
    manager = SecretsManager(
        backends={
            "env": EnvBackend(),
            "keyring": keyring,
            "file": file_be,
        }
    )
    set_default_manager(manager)
    try:
        yield {"manager": manager, "keyring": keyring, "file": file_be}
    finally:
        # 다른 테스트가 OS keyring을 기대하지 않도록 매니저를 초기화한다.
        set_default_manager(None)


def _write_config(
    tmp_path,
    *,
    enabled: bool,
    token_ref: str,
    port: int,
    cors_origins: list[str] | None = None,
) -> str:
    """admin_api 섹션이 포함된 최소 config.yaml을 임시 디렉토리에 만든다."""
    cfg = {
        "admin_api": {
            "enabled": enabled,
            "bind_host": "127.0.0.1",
            "bind_port": port,
            "token_secret": token_ref,
            "read_timeout_seconds": 30,
            "request_max_body_kb": 256,
            "cors_origins": list(cors_origins) if cors_origins is not None else [],
        },
        "agent": {
            "history_limit": 20,
            "max_tool_iterations": 5,
            "db_path": ".agent/conversations.db",
            "workspace_dir": ".agent/workspace",
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 1) 부팅 후 /admin/v1/health 가 Bearer로 200, 미인증은 401
# ---------------------------------------------------------------------------


class TestAdminAPIBootsAndAuth:
    @pytest.mark.asyncio
    async def test_health_returns_200_with_bearer_and_401_without(
        self, tmp_path, isolated_secrets
    ):
        # 1) keyring에 admin_api_token을 등록 — config의 ``keyring:admin_api_token``
        #    참조가 이 값으로 해소된다.
        token = "super-secret-token-for-test"
        isolated_secrets["keyring"].set("admin_api_token", token)

        port = _free_port()
        config_path = _write_config(
            tmp_path,
            enabled=True,
            token_ref="keyring:admin_api_token",
            port=port,
        )

        # 2) 시크릿/감사 모두 격리된 매니저·디렉토리로 주입.
        srv = build_admin_api_server(
            config_path,
            secrets_manager=isolated_secrets["manager"],
            audit_log=AuditLog(tmp_path / "audit"),
            admin_state_dir=tmp_path / "admin",
            health_provider=lambda: {"daemon": {"telegram_running": True}},
        )
        assert srv is not None, "build_admin_api_server should return a server"

        await srv.start()
        try:
            # 3) /admin/v1/health 에 Bearer 토큰으로 호출 → 200.
            url = f"http://127.0.0.1:{port}/admin/v1/health"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers={"Authorization": f"Bearer {token}"}
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["status"] == "ok"
                    # health_provider가 머지된 결과 — daemon 키가 포함된다.
                    assert "daemon" in body
                    assert body["daemon"]["telegram_running"] is True

                # 4) 토큰 누락 → 401.
                async with session.get(url) as resp:
                    assert resp.status == 401

                # 5) 잘못된 토큰 → 401.
                async with session.get(
                    url, headers={"Authorization": "Bearer wrong"}
                ) as resp:
                    assert resp.status == 401
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_build_server_can_host_dashboard_without_8081_listener(
        self, tmp_path, isolated_secrets
    ):
        token = "dashboard-token"
        isolated_secrets["keyring"].set("admin_api_token", token)
        port = _free_port()
        config_path = _write_config(
            tmp_path,
            enabled=True,
            token_ref="keyring:admin_api_token",
            port=port,
        )
        metrics = MetricsCollector()
        metrics.record_execution(success=True, duration_ms=1, tokens_used=11)
        structured = StructuredLogger(log_dir=tmp_path / "logs")
        structured.log(action_type="integration-dashboard", duration_ms=1)

        srv = build_admin_api_server(
            config_path,
            secrets_manager=isolated_secrets["manager"],
            audit_log=AuditLog(tmp_path / "audit"),
            admin_state_dir=tmp_path / "admin",
            dashboard_metrics=metrics,
            dashboard_structured_logger=structured,
        )
        assert srv is not None

        await srv.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/") as resp:
                    assert resp.status == 200
                    assert "SimpleClaw Dashboard" in await resp.text()
                async with session.get(f"http://127.0.0.1:{port}/api/metrics") as resp:
                    assert resp.status == 200
                    assert (await resp.json())["total_tokens_used"] == 11
                async with session.get(
                    f"http://127.0.0.1:{port}/admin/v1/health",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    assert resp.status == 200
        finally:
            await srv.stop()


# ---------------------------------------------------------------------------
# 2) 토큰 미설정 시 부팅 실패 (silent insecure 방지)
# ---------------------------------------------------------------------------


class TestAdminAPIMissingToken:
    @pytest.mark.asyncio
    async def test_missing_token_raises_boot_error(
        self, tmp_path, isolated_secrets
    ):
        # keyring/file/env 어디에도 admin_api_token이 등록돼 있지 않은 상태.
        config_path = _write_config(
            tmp_path,
            enabled=True,
            token_ref="keyring:admin_api_token",  # 해소 불가 — 빈 문자열로 떨어진다.
            port=_free_port(),
        )

        with pytest.raises(AdminAPIBootError) as excinfo:
            build_admin_api_server(
                config_path,
                secrets_manager=isolated_secrets["manager"],
                audit_log=AuditLog(tmp_path / "audit"),
                admin_state_dir=tmp_path / "admin",
            )
        # 에러 메시지에 사유와 발급 가이드가 명시돼 있는지 확인.
        msg = str(excinfo.value)
        assert "admin_api.token_secret" in msg
        assert "admin_api_token" in msg

    @pytest.mark.asyncio
    async def test_empty_string_token_raises_boot_error(
        self, tmp_path, isolated_secrets
    ):
        # 명시적으로 빈 문자열을 박아 둔 경우도 동일하게 실패해야 한다.
        config_path = _write_config(
            tmp_path,
            enabled=True,
            token_ref="",
            port=_free_port(),
        )
        with pytest.raises(AdminAPIBootError):
            build_admin_api_server(
                config_path,
                secrets_manager=isolated_secrets["manager"],
                audit_log=AuditLog(tmp_path / "audit"),
                admin_state_dir=tmp_path / "admin",
            )


# ---------------------------------------------------------------------------
# 3) disabled=false면 None 반환 + 포트 미바인딩
# ---------------------------------------------------------------------------


class TestAdminAPIDisabled:
    @pytest.mark.asyncio
    async def test_disabled_returns_none_and_port_unbound(
        self, tmp_path, isolated_secrets
    ):
        # 토큰을 등록해 두더라도 enabled=false면 서버가 만들어지지 않아야 한다.
        isolated_secrets["keyring"].set("admin_api_token", "ignored")
        port = _free_port()
        config_path = _write_config(
            tmp_path,
            enabled=False,
            token_ref="keyring:admin_api_token",
            port=port,
        )

        srv = build_admin_api_server(
            config_path,
            secrets_manager=isolated_secrets["manager"],
            audit_log=AuditLog(tmp_path / "audit"),
            admin_state_dir=tmp_path / "admin",
        )
        assert srv is None

        # 포트가 실제로 바인딩되지 않았는지 — 같은 포트에 우리가 다시 바인딩 가능해야 한다.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError as exc:  # pragma: no cover — 디버깅 보조.
                pytest.fail(
                    f"disabled mode인데 포트 {port}가 바인딩돼 있다: {exc}"
                )


# ---------------------------------------------------------------------------
# 4) cors_origins 비어 있으면 부팅은 통과 + 경고 로그 (BIZ-246)
# ---------------------------------------------------------------------------


class TestAdminAPICorsOriginsWarning:
    def test_empty_cors_origins_emits_warning(
        self, tmp_path, isolated_secrets, caplog
    ):
        # 토큰은 등록돼 있어 부팅 자체는 성공해야 하지만, cors_origins 가 비어 있어
        # 운영자에게 즉시 보이는 한 줄 경고가 남아야 한다 (silent UI breakage 방지).
        isolated_secrets["keyring"].set("admin_api_token", "warn-test-token")
        config_path = _write_config(
            tmp_path,
            enabled=True,
            token_ref="keyring:admin_api_token",
            port=_free_port(),
            cors_origins=[],
        )

        with caplog.at_level(logging.WARNING, logger="simpleclaw.channels.admin_api_setup"):
            srv = build_admin_api_server(
                config_path,
                secrets_manager=isolated_secrets["manager"],
                audit_log=AuditLog(tmp_path / "audit"),
                admin_state_dir=tmp_path / "admin",
            )

        assert srv is not None, "cors_origins 가 비어도 부팅은 통과해야 한다"
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "cors_origins" in r.getMessage()
        ]
        assert warnings, (
            "cors_origins=[] 일 때 admin_api_setup 이 경고를 남겨야 한다 — "
            f"got records: {[r.getMessage() for r in caplog.records]}"
        )

    def test_populated_cors_origins_silent(
        self, tmp_path, isolated_secrets, caplog
    ):
        # 운영자가 cors_origins 를 명시했으면 경고는 나오지 않아야 한다 — 그래야
        # 정상 상태에서 운영 로그 잡음이 생기지 않는다.
        isolated_secrets["keyring"].set("admin_api_token", "warn-test-token")
        config_path = _write_config(
            tmp_path,
            enabled=True,
            token_ref="keyring:admin_api_token",
            port=_free_port(),
            cors_origins=["http://localhost:8088"],
        )

        with caplog.at_level(logging.WARNING, logger="simpleclaw.channels.admin_api_setup"):
            srv = build_admin_api_server(
                config_path,
                secrets_manager=isolated_secrets["manager"],
                audit_log=AuditLog(tmp_path / "audit"),
                admin_state_dir=tmp_path / "admin",
            )

        assert srv is not None
        cors_warns = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "cors_origins" in r.getMessage()
        ]
        assert not cors_warns, (
            "cors_origins 가 채워져 있으면 경고가 나와서는 안 된다 — "
            f"unexpected: {[r.getMessage() for r in cors_warns]}"
        )
