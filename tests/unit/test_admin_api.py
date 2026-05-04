"""Admin API 단위 테스트 — BIZ-41.

각 엔드포인트가 인증·검증·정책·감사 흐름을 올바르게 따르는지 확인한다.
실제 시스템 상태(데몬, 텔레그램, 시크릿 백엔드)와 분리하기 위해 모든 의존성을
임시 디렉토리·인메모리 백엔드로 격리한다.
"""

from __future__ import annotations


import pytest
import pytest_asyncio
import yaml

from simpleclaw.channels.admin_api import (
    AdminAPIServer,
    _pending_changes_path,
)
from simpleclaw.channels.admin_audit import AuditLog, _is_secret_key, _mask_value
from simpleclaw.channels.admin_policy import (
    HOT,
    PROCESS_RESTART,
    SERVICE_RESTART,
    classify_keys,
    validate_patch,
)
from simpleclaw.security.secrets import SecretsManager


class _InMemoryBackend:
    """단위 테스트용 시크릿 백엔드 — 실제 keyring/파일을 건드리지 않는다."""

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


def _make_secrets_manager() -> SecretsManager:
    """env/keyring/file 모두 인메모리로 대체한 매니저."""
    return SecretsManager(
        backends={
            "env": _InMemoryBackend("env"),
            "keyring": _InMemoryBackend("keyring"),
            "file": _InMemoryBackend("file"),
        }
    )


@pytest.fixture
def tmp_state(tmp_path):
    """config.yaml + admin state + audit dir을 격리된 임시 디렉토리에 둔다."""
    config_path = tmp_path / "config.yaml"
    initial = {
        "llm": {
            "default": "claude",
            "providers": {
                "claude": {
                    "type": "api",
                    "model": "claude-sonnet-4-20250514",
                    "api_key": "keyring:claude_api_key",
                },
            },
        },
        "agent": {
            "history_limit": 20,
            "max_tool_iterations": 5,
            "db_path": ".agent/conversations.db",
            "workspace_dir": ".agent/workspace",
        },
        "webhook": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8080,
            "auth_token": "keyring:webhook_auth_token",
            "rate_limit": 60,
        },
        "telegram": {
            "bot_token": "keyring:telegram_bot_token",
            "whitelist": {"user_ids": [], "chat_ids": []},
        },
        "daemon": {
            "heartbeat_interval": 300,
            "db_path": ".agent/daemon.db",
        },
    }
    config_path.write_text(yaml.safe_dump(initial), encoding="utf-8")
    return {
        "config_path": config_path,
        "state_dir": tmp_path / "admin",
        "audit_dir": tmp_path / "audit",
    }


@pytest.fixture
def server(tmp_state):
    return AdminAPIServer(
        auth_token="test-token",
        config_path=tmp_state["config_path"],
        audit_log=AuditLog(tmp_state["audit_dir"]),
        secrets_manager=_make_secrets_manager(),
        admin_state_dir=tmp_state["state_dir"],
    )


@pytest_asyncio.fixture
async def client(server, aiohttp_client):
    return await aiohttp_client(server.get_app())


HEADERS = {"Authorization": "Bearer test-token"}


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, client):
        resp = await client.get("/admin/v1/config")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_wrong_token_returns_401(self, client):
        resp = await client.get(
            "/admin/v1/config", headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_valid_token_passes(self, client):
        resp = await client.get("/admin/v1/config", headers=HEADERS)
        assert resp.status == 200


# ---------------------------------------------------------------------------
# Config GET
# ---------------------------------------------------------------------------


class TestConfigGet:
    @pytest.mark.asyncio
    async def test_get_full_config(self, client):
        resp = await client.get("/admin/v1/config", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        assert "config" in data
        assert data["config"]["llm"]["default"] == "claude"
        # 시크릿 참조는 마스킹되지 않는다 (그 자체가 비밀이 아니므로).
        assert (
            data["config"]["llm"]["providers"]["claude"]["api_key"]
            == "keyring:claude_api_key"
        )

    @pytest.mark.asyncio
    async def test_get_area_config(self, client):
        resp = await client.get("/admin/v1/config/llm", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        assert data["area"] == "llm"
        assert "providers" in data["config"]

    @pytest.mark.asyncio
    async def test_get_unknown_area_404(self, client):
        resp = await client.get("/admin/v1/config/unknown", headers=HEADERS)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_channels_alias_returns_telegram_and_webhook(self, client):
        resp = await client.get("/admin/v1/config/channels", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        assert "telegram" in data["config"]
        assert "webhook" in data["config"]


# ---------------------------------------------------------------------------
# Config PATCH — 검증, dry-run, 적용, pending
# ---------------------------------------------------------------------------


class TestConfigPatch:
    @pytest.mark.asyncio
    async def test_validation_failure_returns_422(self, client):
        resp = await client.patch(
            "/admin/v1/config/agent",
            headers=HEADERS,
            json={"history_limit": 9999},  # > 200
        )
        assert resp.status == 422
        data = await resp.json()
        assert "errors" in data
        assert any("history_limit" in e for e in data["errors"])

    @pytest.mark.asyncio
    async def test_dry_run_does_not_persist(self, client, tmp_state):
        resp = await client.patch(
            "/admin/v1/config/agent?dry_run=true",
            headers=HEADERS,
            json={"history_limit": 50},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["outcome"] == "dry_run"
        assert "diff" in data
        assert data["policy"]["level"] == HOT

        # 실제 파일은 변경되지 않았는지 확인.
        on_disk = yaml.safe_load(tmp_state["config_path"].read_text())
        assert on_disk["agent"]["history_limit"] == 20

    @pytest.mark.asyncio
    async def test_hot_change_applies_immediately(self, client, tmp_state):
        resp = await client.patch(
            "/admin/v1/config/agent",
            headers=HEADERS,
            json={"history_limit": 50},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["outcome"] == "applied"
        assert data["policy"]["level"] == HOT
        assert "audit_id" in data

        on_disk = yaml.safe_load(tmp_state["config_path"].read_text())
        assert on_disk["agent"]["history_limit"] == 50

    @pytest.mark.asyncio
    async def test_process_restart_change_goes_to_pending(self, client, tmp_state):
        resp = await client.patch(
            "/admin/v1/config/agent",
            headers=HEADERS,
            json={"db_path": "/new/path/db"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["outcome"] == "pending"
        assert data["policy"]["level"] == PROCESS_RESTART
        assert data["policy"]["requires_restart"] is True

        # config.yaml은 그대로.
        on_disk = yaml.safe_load(tmp_state["config_path"].read_text())
        assert on_disk["agent"]["db_path"] == ".agent/conversations.db"

        # 펜딩 파일에는 새 값이 들어있다.
        pending = yaml.safe_load(
            _pending_changes_path(tmp_state["state_dir"]).read_text()
        )
        assert pending["agent"]["db_path"] == "/new/path/db"

    @pytest.mark.asyncio
    async def test_service_restart_change_persists_with_flag(self, client, tmp_state):
        resp = await client.patch(
            "/admin/v1/config/telegram",
            headers=HEADERS,
            json={"bot_token": "keyring:new_token"},
        )
        assert resp.status == 200
        data = await resp.json()
        # admin_policy: telegram.bot_token = SERVICE_RESTART → 즉시 yaml 반영 + flag.
        assert data["outcome"] == "applied"
        assert data["policy"]["level"] == SERVICE_RESTART
        assert data["policy"]["requires_restart"] is True

        on_disk = yaml.safe_load(tmp_state["config_path"].read_text())
        assert on_disk["telegram"]["bot_token"] == "keyring:new_token"

    @pytest.mark.asyncio
    async def test_audit_records_dry_run(self, client, tmp_state):
        await client.patch(
            "/admin/v1/config/agent?dry_run=1",
            headers=HEADERS,
            json={"history_limit": 50},
        )
        audit = AuditLog(tmp_state["audit_dir"])
        entries = audit.search()
        assert len(entries) == 1
        assert entries[0].outcome == "dry_run"

    @pytest.mark.asyncio
    async def test_unknown_area_returns_404(self, client):
        resp = await client.patch(
            "/admin/v1/config/unknown",
            headers=HEADERS,
            json={"foo": "bar"},
        )
        assert resp.status == 404


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class TestSecrets:
    @pytest.mark.asyncio
    async def test_list_secrets_returns_metadata(self, client, server):
        # 시크릿 두 개를 등록.
        server._secrets.store("keyring", "alpha", "value-a")
        server._secrets.store("keyring", "beta", "value-b")

        resp = await client.get("/admin/v1/secrets", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        names = {item["name"] for item in data["secrets"]}
        assert {"alpha", "beta"}.issubset(names)
        # 평문 노출 금지.
        for item in data["secrets"]:
            assert "value" not in item

    @pytest.mark.asyncio
    async def test_reveal_returns_plaintext_with_nonce(self, client, server):
        server._secrets.store("keyring", "to_reveal", "plain-value")
        resp = await client.post(
            "/admin/v1/secrets/to_reveal/reveal",
            headers=HEADERS,
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["value"] == "plain-value"
        assert data["nonce"]
        assert data["expires_in_seconds"] == 15

    @pytest.mark.asyncio
    async def test_reveal_unknown_returns_404(self, client):
        resp = await client.post(
            "/admin/v1/secrets/nope/reveal",
            headers=HEADERS,
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_rotate_secret(self, client, server):
        resp = await client.post(
            "/admin/v1/secrets/api_key/rotate",
            headers=HEADERS,
            json={"value": "new-key", "backend": "keyring"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["outcome"] == "applied"
        assert server._secrets.get_backend("keyring").get("api_key") == "new-key"

    @pytest.mark.asyncio
    async def test_rotate_secret_audit_masks_value(self, client, server, tmp_state):
        await client.post(
            "/admin/v1/secrets/api_key/rotate",
            headers=HEADERS,
            json={"value": "super-secret-1234", "backend": "keyring"},
        )
        entries = AuditLog(tmp_state["audit_dir"]).search(action="secret.rotate")
        assert entries
        # value는 _mask_secrets 패턴에 의해 마스킹돼야 함.
        # after는 dict {"value": ...} 형태이며, 키 이름이 'value'라 자체로는 마스킹 패턴이
        # 아닐 수 있으나, target에서 시크릿 이름을 별도 보존하는 게 핵심.
        assert "super-secret-1234" not in entries[0].to_json()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    @pytest.mark.asyncio
    async def test_search_filters_by_outcome(self, client, tmp_state):
        # 1) dry_run, 2) applied 두 항목 만들기.
        await client.patch(
            "/admin/v1/config/agent?dry_run=true",
            headers=HEADERS,
            json={"history_limit": 30},
        )
        await client.patch(
            "/admin/v1/config/agent",
            headers=HEADERS,
            json={"history_limit": 30},
        )

        resp = await client.get(
            "/admin/v1/audit?outcome=applied", headers=HEADERS
        )
        assert resp.status == 200
        data = await resp.json()
        assert all(e["outcome"] == "applied" for e in data["entries"])

    @pytest.mark.asyncio
    async def test_undo_unknown_id_returns_404(self, client):
        resp = await client.post(
            "/admin/v1/audit/00000000-0000-0000-0000-000000000000/undo",
            headers=HEADERS,
        )
        assert resp.status == 404


# ---------------------------------------------------------------------------
# 헬스 / 시스템
# ---------------------------------------------------------------------------


class TestHealthAndSystem:
    @pytest.mark.asyncio
    async def test_health_returns_snapshot(self, client):
        resp = await client.get("/admin/v1/health", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "metrics" in data
        assert "pending_changes" in data

    @pytest.mark.asyncio
    async def test_system_info_returns_diagnostics(self, client, tmp_state):
        resp = await client.get("/admin/v1/system/info", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        # 필수 키 — System 화면 좌측 카드가 의존하는 계약.
        for key in (
            "version",
            "python_version",
            "platform",
            "pid",
            "uptime_seconds",
            "config_path",
            "db_path",
            "host",
            "port",
        ):
            assert key in data, f"missing key: {key}"
        assert data["pid"] > 0
        assert data["config_path"] == str(tmp_state["config_path"])
        # 디스크 정보는 OS 호출 실패 가능성 때문에 nullable이지만, 임시 디렉토리에서는
        # 항상 채워진다.
        assert data["disk"] is not None
        assert {"path", "total_bytes", "used_bytes", "free_bytes"} <= data["disk"].keys()

    @pytest.mark.asyncio
    async def test_system_info_requires_auth(self, client):
        resp = await client.get("/admin/v1/system/info")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_system_restart_merges_pending_into_yaml(
        self, client, tmp_state
    ):
        # 1) Process-restart 변경을 만들어 펜딩으로 보낸다.
        await client.patch(
            "/admin/v1/config/agent",
            headers=HEADERS,
            json={"db_path": "/replaced/db"},
        )
        # 2) 시스템 재시작 호출 — 콜백 없이도 펜딩이 머지되어야 한다.
        resp = await client.post(
            "/admin/v1/system/restart",
            headers=HEADERS,
            json={"reason": "test"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["outcome"] == "applied"
        assert data["applied_pending"] >= 1
        on_disk = yaml.safe_load(tmp_state["config_path"].read_text())
        assert on_disk["agent"]["db_path"] == "/replaced/db"
        assert (
            not _pending_changes_path(tmp_state["state_dir"]).exists()
        )

    @pytest.mark.asyncio
    async def test_system_restart_invokes_callback(
        self, tmp_state, aiohttp_client
    ):
        captured: list[dict] = []

        def cb(body):
            captured.append(body)

        srv = AdminAPIServer(
            auth_token="t",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            restart_callback=cb,
        )
        c = await aiohttp_client(srv.get_app())
        resp = await c.post(
            "/admin/v1/system/restart",
            headers={"Authorization": "Bearer t"},
            json={"reason": "ops"},
        )
        assert resp.status == 200
        assert captured == [{"reason": "ops"}]


# ---------------------------------------------------------------------------
# 정책 엔진 단위 테스트 — admin_policy 모듈.
# ---------------------------------------------------------------------------


class TestPolicyEngine:
    def test_hot_for_history_limit(self):
        result = classify_keys("agent", {"history_limit": 30})
        assert result.level == HOT
        assert "agent.orchestrator" in result.affected_modules

    def test_process_restart_for_db_path(self):
        result = classify_keys("agent", {"db_path": "/foo"})
        assert result.level == PROCESS_RESTART
        assert result.requires_restart is True

    def test_service_restart_for_telegram_token(self):
        result = classify_keys("telegram", {"bot_token": "keyring:t"})
        assert result.level == SERVICE_RESTART

    def test_max_level_wins_when_mixed(self):
        result = classify_keys(
            "agent",
            {"history_limit": 30, "db_path": "/x"},
        )
        # 둘 중 더 보수적인 PROCESS_RESTART 채택.
        assert result.level == PROCESS_RESTART

    def test_hot_for_llm_routing(self):
        # BIZ-45 — 카테고리별 라우팅은 다음 호출부터 즉시 적용되므로 Hot.
        result = classify_keys(
            "llm",
            {"routing": {"general": "claude", "coding": "claude"}},
        )
        assert result.level == HOT
        assert "llm.router" in result.affected_modules


class TestValidation:
    def test_history_limit_range(self):
        assert validate_patch("agent", {"history_limit": 5}) == []
        errors = validate_patch("agent", {"history_limit": 999})
        assert errors

    def test_telegram_token_regex(self):
        assert validate_patch(
            "telegram", {"bot_token": "keyring:foo"}
        ) == []
        # 평문 잘못된 토큰은 실패.
        errors = validate_patch("telegram", {"bot_token": "plain-bad"})
        assert errors

    def test_webhook_port_range(self):
        assert validate_patch("webhook", {"port": 8080}) == []
        assert validate_patch("webhook", {"port": 80})

    def test_voice_output_format_enum(self):
        assert validate_patch(
            "voice", {"tts": {"output_format": "mp3"}}
        ) == []
        assert validate_patch(
            "voice", {"tts": {"output_format": "wav"}}
        )

    def test_llm_routing_value_must_match_provider_when_full_patch(self):
        # 동일 PATCH에 providers가 함께 들어오면 화이트리스트 검증.
        errors = validate_patch(
            "llm",
            {
                "providers": {"claude": {"type": "api"}},
                "routing": {"general": "openai"},
            },
        )
        assert errors

    def test_llm_routing_partial_patch_skips_whitelist(self):
        # routing만 단독 patch — 백엔드는 기존 providers를 모르므로 검증 생략.
        assert validate_patch("llm", {"routing": {"general": "claude"}}) == []

    def test_llm_routing_value_must_be_string(self):
        errors = validate_patch("llm", {"routing": {"general": 1}})
        assert errors

    def test_llm_default_must_exist_in_providers(self):
        errors = validate_patch(
            "llm",
            {
                "default": "ghost",
                "providers": {"claude": {"model": "x"}},
            },
        )
        assert errors


class TestMasking:
    def test_is_secret_key_matches_common_patterns(self):
        for key in ("api_key", "bot_token", "auth_token", "client_secret"):
            assert _is_secret_key(key)

    def test_is_secret_key_rejects_normal(self):
        for key in ("model", "default", "host", "user_ids"):
            assert not _is_secret_key(key)

    def test_mask_value_keeps_reference_strings(self):
        assert _mask_value("keyring:foo") == "keyring:foo"
        assert _mask_value("env:ANTHROPIC_API_KEY") == "env:ANTHROPIC_API_KEY"

    def test_mask_value_truncates_plaintext(self):
        assert _mask_value("super-secret-1234") == "••••1234"

    def test_mask_value_short_string(self):
        assert _mask_value("ab") == "••••"


# ---------------------------------------------------------------------------
# Logs (when no logger injected)
# ---------------------------------------------------------------------------


class TestLogs:
    @pytest.mark.asyncio
    async def test_logs_returns_empty_when_no_logger(self, client):
        resp = await client.get("/admin/v1/logs", headers=HEADERS)
        assert resp.status == 200
        data = await resp.json()
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_logs_uses_injected_logger(self, tmp_state, aiohttp_client):
        class FakeLogger:
            def get_entries(self, **kwargs):
                # 시그니처 호환만 보장.
                return [
                    {
                        "level": "INFO",
                        "action_type": "agent.message",
                        "trace_id": "abc",
                    },
                    {
                        "level": "ERROR",
                        "action_type": "skill.run",
                        "trace_id": "abc",
                    },
                ]

        srv = AdminAPIServer(
            auth_token="t",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            structured_logger=FakeLogger(),
        )
        c = await aiohttp_client(srv.get_app())
        resp = await c.get(
            "/admin/v1/logs?level=ERROR",
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["level"] == "ERROR"


# ---------------------------------------------------------------------------
# 채널 테스트 발송 — POST /admin/v1/channels/{name}/test
# ---------------------------------------------------------------------------


class TestChannelTest:
    """``channel_test_callback``을 주입해 외부 네트워크 의존을 끊고 검증한다."""

    @pytest.mark.asyncio
    async def test_telegram_callback_returns_status_and_audit(
        self, tmp_state, aiohttp_client
    ):
        captured: list[tuple[str, dict]] = []

        async def fake_cb(name: str, options: dict) -> dict:
            captured.append((name, options))
            return {
                "ok": True,
                "status_code": 200,
                "latency_ms": 42,
                "target": "12345",
            }

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            channel_test_callback=fake_cb,
        )
        c = await aiohttp_client(srv.get_app())

        resp = await c.post(
            "/admin/v1/channels/telegram/test",
            headers=HEADERS,
            json={"message": "ping"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["status_code"] == 200
        assert data["latency_ms"] == 42
        assert data["target"] == "12345"
        assert "audit_id" in data

        # 콜백에 채널명과 옵션이 정확히 전달되었는지.
        assert captured == [("telegram", {"message": "ping"})]

        # 감사 엔트리가 ``channel.test`` action으로 기록됐는지.
        audit = AuditLog(tmp_state["audit_dir"])
        entries = audit.search()
        assert len(entries) == 1
        assert entries[0].action == "channel.test"
        assert entries[0].area == "channels"
        assert entries[0].target == "telegram"
        assert entries[0].outcome == "applied"

    @pytest.mark.asyncio
    async def test_unknown_channel_returns_404(self, client):
        resp = await client.post(
            "/admin/v1/channels/discord/test",
            headers=HEADERS,
            json={"message": "ping"},
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_default_message_when_body_missing(
        self, tmp_state, aiohttp_client
    ):
        seen: dict = {}

        def sync_cb(name: str, options: dict) -> dict:
            seen.update(options)
            return {"ok": True, "status_code": 200, "latency_ms": 1}

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            channel_test_callback=sync_cb,
        )
        c = await aiohttp_client(srv.get_app())

        # 본문 없이 호출 → 기본 메시지가 사용돼야 한다.
        resp = await c.post(
            "/admin/v1/channels/webhook/test",
            headers=HEADERS,
        )
        assert resp.status == 200
        assert seen.get("message") == "Hello from admin"

    @pytest.mark.asyncio
    async def test_callback_failure_records_rejected_audit(
        self, tmp_state, aiohttp_client
    ):
        async def failing_cb(name: str, options: dict) -> dict:
            return {
                "ok": False,
                "status_code": 502,
                "latency_ms": 100,
                "error": "bad gateway",
            }

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            channel_test_callback=failing_cb,
        )
        c = await aiohttp_client(srv.get_app())

        resp = await c.post(
            "/admin/v1/channels/webhook/test",
            headers=HEADERS,
            json={"message": "x"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is False
        assert data["status_code"] == 502
        assert data["error"] == "bad gateway"

        audit = AuditLog(tmp_state["audit_dir"])
        entries = audit.search()
        assert len(entries) == 1
        assert entries[0].outcome == "rejected"
        assert entries[0].reason == "bad gateway"

    @pytest.mark.asyncio
    async def test_callback_exception_normalized(self, tmp_state, aiohttp_client):
        async def boom_cb(name: str, options: dict) -> dict:
            raise RuntimeError("boom")

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            channel_test_callback=boom_cb,
        )
        c = await aiohttp_client(srv.get_app())

        resp = await c.post(
            "/admin/v1/channels/telegram/test",
            headers=HEADERS,
            json={"message": "x"},
        )
        # 콜백 예외는 200 응답에 ``ok=False`` + ``error``로 정규화된다.
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is False
        assert "boom" in (data.get("error") or "")


# ---------------------------------------------------------------------------
# BIZ-77 — 인사이트 source 역추적 엔드포인트
# ---------------------------------------------------------------------------


class TestInsightSources:
    """``GET /admin/v1/memory/insights/{topic}/sources`` 동작 검증.

    핵심 케이스:
    - 의존성(conversation_store/insight_store) 미주입 → 503 (silent 404 보다 명시적).
    - 빈 topic → 422.
    - 미존재 topic → 404.
    - 정상 → 200, sources 배열에 메시지 id/role/content/timestamp/channel 포함.
    """

    @pytest.mark.asyncio
    async def test_returns_503_when_dependencies_missing(self, client):
        """기본 server 픽스처는 두 의존성을 주입하지 않으므로 503 이어야 한다."""
        resp = await client.get(
            "/admin/v1/memory/insights/anytopic/sources", headers=HEADERS
        )
        assert resp.status == 503
        body = await resp.json()
        assert "not configured" in body["error"]

    @pytest_asyncio.fixture
    async def insight_client(self, tmp_state, tmp_path, aiohttp_client):
        """InsightStore + ConversationStore 가 주입된 server 클라이언트.

        sidecar 1건과 메시지 2건을 미리 적재해 happy-path 검증에 사용한다.
        """
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.insights import InsightMeta, InsightStore
        from simpleclaw.memory.models import ConversationMessage, MessageRole

        conv = ConversationStore(tmp_path / "conv.db")
        # 두 채널에서 들어온 메시지 — 응답에 channel 필드가 그대로 노출되는지 확인.
        id1 = conv.add_message(
            ConversationMessage(
                role=MessageRole.USER, content="뉴스 보여줘", channel="telegram",
            )
        )
        id2 = conv.add_message(
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content="네, 정치 뉴스를 알려드릴게요.",
                channel="telegram",
            )
        )
        # 인사이트 메타: 두 메시지가 source.
        sidecar_path = tmp_path / "insights.jsonl"
        insights = InsightStore(sidecar_path)
        meta = InsightMeta(
            topic="정치뉴스",
            text="정치 뉴스 관심",
            evidence_count=2,
            confidence=0.55,
            source_msg_ids=[id1, id2],
        )
        meta.recompute_id_range()
        insights.save_all({"정치뉴스": meta})

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            conversation_store=conv,
            insight_store=insights,
        )
        client = await aiohttp_client(srv.get_app())
        return client, id1, id2

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_topic(self, insight_client):
        client, _, _ = insight_client
        resp = await client.get(
            "/admin/v1/memory/insights/없는토픽/sources", headers=HEADERS
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_returns_sources_for_existing_topic(self, insight_client):
        client, id1, id2 = insight_client
        resp = await client.get(
            "/admin/v1/memory/insights/정치뉴스/sources", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["topic"] == "정치뉴스"
        assert body["evidence_count"] == 2
        assert body["start_msg_id"] == id1
        assert body["end_msg_id"] == id2
        assert body["source_msg_ids"] == [id1, id2]

        sources = body["sources"]
        assert len(sources) == 2
        # 시간순(id 오름차순) 정렬을 가정.
        assert sources[0]["id"] == id1
        assert sources[0]["role"] == "user"
        assert sources[0]["content"] == "뉴스 보여줘"
        assert sources[0]["channel"] == "telegram"
        assert "timestamp" in sources[0]
        assert sources[1]["id"] == id2
        assert sources[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_normalized_topic_resolves_same_row(self, insight_client):
        """원문/정규형 어느 쪽으로 와도 같은 행을 가리켜야 한다."""
        client, id1, _ = insight_client
        # 정규화 후 같은 키가 되도록 공백/구두점만 다르게.
        resp = await client.get(
            "/admin/v1/memory/insights/정치 뉴스!/sources", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["start_msg_id"] == id1


# ---------------------------------------------------------------------------
# BIZ-79 — pending suggestion 큐 + accept/edit/reject + blocklist
# ---------------------------------------------------------------------------


class TestSuggestionQueue:
    """``/admin/v1/memory/suggestions/...`` 라우트 검증.

    DoD 회귀:
    - 의존성 (suggestion_store/blocklist_store/writer) 미주입 → 503
    - GET /memory/suggestions — pending 목록
    - accept → writer 호출, 행 status=accepted, terminal 재호출 시 409
    - edit → writer 호출 (edited_text 사용), 행 status=edited
    - reject → blocklist add, 행 status=rejected, 다음 사이클 차단
    """

    @pytest.mark.asyncio
    async def test_returns_503_when_suggestion_store_missing(self, client):
        """기본 server 픽스처는 큐를 주입하지 않으므로 503."""
        resp = await client.get(
            "/admin/v1/memory/suggestions", headers=HEADERS
        )
        assert resp.status == 503

    @pytest_asyncio.fixture
    async def suggestion_client(self, tmp_state, tmp_path, aiohttp_client):
        """SuggestionStore + BlocklistStore + writer 가 주입된 클라이언트.

        대화 메시지 1건 + pending suggestion 2건을 미리 적재한다 (1건은 USER.md
        accept 검증용, 1건은 reject 검증용).
        """
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.insights import InsightMeta
        from simpleclaw.memory.models import ConversationMessage, MessageRole
        from simpleclaw.memory.suggestions import (
            BlocklistStore,
            SuggestionStore,
        )

        conv = ConversationStore(tmp_path / "conv.db")
        mid = conv.add_message(
            ConversationMessage(
                role=MessageRole.USER,
                content="정치 뉴스 보여줘",
                channel="cli",
            )
        )

        sugg_path = tmp_path / "sugg.jsonl"
        sugg_store = SuggestionStore(sugg_path)
        m1 = InsightMeta(
            topic="정치뉴스",
            text="정치 뉴스에 관심",
            evidence_count=1,
            confidence=0.4,
            source_msg_ids=[mid],
        )
        m1.recompute_id_range()
        s1 = sugg_store.upsert_pending(m1)

        m2 = InsightMeta(
            topic="스팸토픽",
            text="스팸 데이터",
            evidence_count=1,
            confidence=0.4,
            source_msg_ids=[mid],
        )
        m2.recompute_id_range()
        s2 = sugg_store.upsert_pending(m2)

        bl_path = tmp_path / "bl.jsonl"
        bl_store = BlocklistStore(bl_path)

        # USER.md writer — 호출 인자를 캡처해 검증할 수 있게 list 에 누적.
        applied_calls: list[str] = []

        def writer(text: str) -> None:
            applied_calls.append(text)

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            conversation_store=conv,
            suggestion_store=sugg_store,
            blocklist_store=bl_store,
            suggestion_writer=writer,
        )
        client = await aiohttp_client(srv.get_app())
        return client, sugg_store, bl_store, applied_calls, s1.id, s2.id, mid

    @pytest.mark.asyncio
    async def test_list_pending(self, suggestion_client):
        client, _, _, _, s1_id, s2_id, _ = suggestion_client
        resp = await client.get(
            "/admin/v1/memory/suggestions", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["total"] == 2
        assert body["pending_count"] == 2
        ids = {s["id"] for s in body["suggestions"]}
        assert ids == {s1_id, s2_id}

    @pytest.mark.asyncio
    async def test_accept_appends_text_and_marks_terminal(
        self, suggestion_client
    ):
        """accept → writer 호출, 행 status=accepted, 재호출 시 409."""
        client, sugg_store, _, applied, s1_id, _, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s1_id}/accept", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "accepted"
        assert applied == ["정치 뉴스에 관심"]

        # 재호출 시 409 (terminal).
        resp2 = await client.post(
            f"/admin/v1/memory/suggestions/{s1_id}/accept", headers=HEADERS
        )
        assert resp2.status == 409

    @pytest.mark.asyncio
    async def test_edit_uses_supplied_text(self, suggestion_client):
        """edit → body.text 가 USER.md 에 적용된다."""
        client, _, _, applied, s1_id, _, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s1_id}/edit",
            headers=HEADERS,
            json={"text": "정치 뉴스 — 사용자 보정"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "edited"
        assert body["edited_text"] == "정치 뉴스 — 사용자 보정"
        assert applied == ["정치 뉴스 — 사용자 보정"]

    @pytest.mark.asyncio
    async def test_edit_requires_text_field(self, suggestion_client):
        client, _, _, _, s1_id, _, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s1_id}/edit",
            headers=HEADERS,
            json={"text": "  "},
        )
        assert resp.status == 422

    @pytest.mark.asyncio
    async def test_reject_adds_to_blocklist(self, suggestion_client):
        """reject → blocklist 에 토픽 추가 + suggestion status=rejected."""
        client, sugg_store, bl_store, applied, _, s2_id, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s2_id}/reject",
            headers=HEADERS,
            json={"reason": "스팸"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "rejected"
        assert body["reject_reason"] == "스팸"
        # writer 는 호출되지 않는다.
        assert applied == []
        # blocklist 에 추가됐다 — period 미지정은 영구.
        assert bl_store.is_blocked("스팸토픽")
        entry = bl_store.load().get("스팸토픽")
        assert entry is not None
        assert "expires_at" not in entry  # 영구 차단
        # suggestion 행도 rejected.
        s = sugg_store.get(s2_id)
        assert s is not None and s.status == "rejected"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("period_days", [30, 90, 180])
    async def test_reject_with_blocklist_period_sets_ttl(
        self, suggestion_client, period_days
    ):
        """BIZ-93: blocklist_period_days 30/90/180 → expires_at 기록."""
        client, _, bl_store, _, _, s2_id, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s2_id}/reject",
            headers=HEADERS,
            json={
                "reason": "스팸",
                "blocklist_period_days": period_days,
            },
        )
        assert resp.status == 200
        entry = bl_store.load().get("스팸토픽")
        assert entry is not None
        assert entry["ttl_seconds"] == period_days * 86400
        assert "expires_at" in entry  # ISO 문자열로 저장
        # 차단 상태가 현재 시점 기준으로 유효해야 한다.
        assert bl_store.is_blocked("스팸토픽")

    @pytest.mark.asyncio
    async def test_reject_with_null_period_is_permanent(
        self, suggestion_client
    ):
        """BIZ-93: blocklist_period_days=null 은 영구 차단."""
        client, _, bl_store, _, _, s2_id, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s2_id}/reject",
            headers=HEADERS,
            json={
                "reason": "영구",
                "blocklist_period_days": None,
            },
        )
        assert resp.status == 200
        entry = bl_store.load().get("스팸토픽")
        assert entry is not None
        assert "expires_at" not in entry
        assert "ttl_seconds" not in entry

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_value", [7, 365, "abc", -1, 0])
    async def test_reject_invalid_period_returns_422(
        self, suggestion_client, bad_value
    ):
        """BIZ-93: 30/90/180/null 외 값은 422 반환."""
        client, _, _, _, _, s2_id, _ = suggestion_client
        resp = await client.post(
            f"/admin/v1/memory/suggestions/{s2_id}/reject",
            headers=HEADERS,
            json={"blocklist_period_days": bad_value},
        )
        assert resp.status == 422

    @pytest.mark.asyncio
    async def test_get_sources_returns_messages(self, suggestion_client):
        """suggestion 의 source_msg_ids 가 가리키는 메시지를 응답에 포함."""
        client, _, _, _, s1_id, _, mid = suggestion_client
        resp = await client.get(
            f"/admin/v1/memory/suggestions/{s1_id}/sources",
            headers=HEADERS,
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["suggestion"]["id"] == s1_id
        assert len(body["sources"]) == 1
        assert body["sources"][0]["id"] == mid
        assert body["sources"][0]["content"] == "정치 뉴스 보여줘"

    @pytest.mark.asyncio
    async def test_unknown_id_returns_404(self, suggestion_client):
        client, *_ = suggestion_client
        resp = await client.post(
            "/admin/v1/memory/suggestions/no-such-id/accept",
            headers=HEADERS,
        )
        assert resp.status == 404

# ---------------------------------------------------------------------------
# BIZ-81 — 드리밍 운영 관측성 (/memory/dreaming/runs, /memory/dreaming/status)
# ---------------------------------------------------------------------------


class TestDreamingObservability:
    """Admin API 의 dreaming 메트릭/상태 엔드포인트.

    검증:
    - dreaming_run_store 미주입 → /runs 가 503 (운영자가 disabled 사실 인지 가능).
    - 메트릭이 적재되면 최신순으로 limit 적용해 반환.
    - /status 가 last_run / last_successful_run / 7일 KPI / rejection rate 를
      한 호출에 모아서 응답 (UI 가 호출 1회로 KPI 패널을 그릴 수 있어야).
    - status_provider 의 next_run / blockers 가 그대로 응답에 들어간다.
    - run_store 가 None 이어도 /status 자체는 200 (metrics_enabled=False 로 표시).
    """

    @pytest_asyncio.fixture
    async def runs_client(self, tmp_state, tmp_path, aiohttp_client):
        """DreamingRunStore + status provider 가 주입된 클라이언트.

        4건의 메트릭 행을 미리 적재 (success / skip / error / 윈도우 밖) — KPI 집계
        검증용.
        """
        from datetime import datetime, timedelta

        from simpleclaw.memory.dreaming_runs import (
            SKIP_NO_MESSAGES,
            DreamingRunRecord,
            DreamingRunStore,
        )

        runs_path = tmp_path / "runs.jsonl"
        run_store = DreamingRunStore(runs_path)
        now = datetime.now()
        # 행은 chronological(오래된 → 최신) 으로 append — store 는 파일 순서를 시간순으로 가정.
        # 윈도우 밖 (8일 전) — 7일 KPI 에서 제외되어야 한다.
        run_store.append(
            DreamingRunRecord(
                started_at=now - timedelta(days=8),
                ended_at=now - timedelta(days=8, seconds=-1),
                input_msg_count=99,
            )
        )
        run_store.append(
            DreamingRunRecord(
                started_at=now - timedelta(days=2),
                ended_at=now - timedelta(days=2, seconds=-1),
                error="boom",
            )
        )
        run_store.append(
            DreamingRunRecord(
                started_at=now - timedelta(days=1),
                ended_at=now - timedelta(days=1, seconds=-1),
                skip_reason=SKIP_NO_MESSAGES,
            )
        )
        run_store.append(
            DreamingRunRecord(
                started_at=now - timedelta(hours=2),
                ended_at=now - timedelta(hours=2, seconds=-3),
                input_msg_count=12,
                generated_insight_count=2,
                rejected_count=0,
            )
        )

        next_run_iso = (now + timedelta(hours=14)).isoformat()

        def status_provider() -> dict:
            return {
                "next_run": next_run_iso,
                "overnight_hour": 3,
                "idle_threshold_seconds": 7200,
                "trigger_blockers": ["오늘 이미 실행됨"],
                "trigger_message": "오늘 03:00 이후 한 번 실행됐습니다.",
            }

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            dreaming_run_store=run_store,
            dreaming_status_provider=status_provider,
        )
        client = await aiohttp_client(srv.get_app())
        return client, run_store, next_run_iso

    @pytest.mark.asyncio
    async def test_runs_returns_503_when_run_store_missing(self, client):
        """기본 fixture 는 run_store 미주입 — 명시적 503 응답."""
        resp = await client.get(
            "/admin/v1/memory/dreaming/runs", headers=HEADERS
        )
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_runs_returns_recent_records_newest_first(self, runs_client):
        client, _, _ = runs_client
        resp = await client.get(
            "/admin/v1/memory/dreaming/runs?limit=2", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["total"] == 2
        # 최신 (2시간 전 success) 가 첫 번째.
        assert body["runs"][0]["status"] == "success"
        assert body["runs"][0]["input_msg_count"] == 12
        # 두 번째는 1일 전 skip.
        assert body["runs"][1]["status"] == "skip"
        assert body["runs"][1]["skip_reason"] == "no_messages"

    @pytest.mark.asyncio
    async def test_runs_limit_clamped(self, runs_client):
        client, _, _ = runs_client
        # limit=999 → 최대 200 으로 클램프, 가용 4건 모두 반환.
        resp = await client.get(
            "/admin/v1/memory/dreaming/runs?limit=999", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["total"] == 4

    @pytest.mark.asyncio
    async def test_runs_invalid_limit_falls_back_to_default(self, runs_client):
        client, _, _ = runs_client
        resp = await client.get(
            "/admin/v1/memory/dreaming/runs?limit=abc", headers=HEADERS
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_status_aggregates_kpi_and_provider_state(self, runs_client):
        """/status 가 KPI + last_run + provider 상태를 한 응답으로 합쳐야 한다."""
        client, _, next_run_iso = runs_client
        resp = await client.get(
            "/admin/v1/memory/dreaming/status", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["metrics_enabled"] is True

        # last_run = 가장 최근 (2시간 전 success)
        assert body["last_run"] is not None
        assert body["last_run"]["status"] == "success"
        # last_successful_run 도 같은 행 (다른 행이 모두 success 가 아님).
        assert body["last_successful_run"] is not None
        assert body["last_successful_run"]["input_msg_count"] == 12

        # next_run / blockers — provider 가 준 값이 그대로.
        assert body["next_run"] == next_run_iso
        assert body["overnight_hour"] == 3
        assert body["idle_threshold_seconds"] == 7200
        assert "오늘 이미 실행됨" in body["trigger_blockers"]
        assert body["trigger_message"]

        # 7일 KPI — 4건 중 3건이 윈도우 안 (success 1, skip 1, error 1).
        kpi = body["kpi_7d"]
        assert kpi is not None
        assert kpi["total_runs"] == 3
        assert kpi["success"] == 1
        assert kpi["skip"] == 1
        assert kpi["error"] == 1
        assert kpi["input_msg_total"] == 12  # 윈도우 밖 99 제외
        assert kpi["skip_breakdown"] == {"no_messages": 1}

        # rejection rate — suggestion_store 미주입 → reviewed=0, rate=None.
        assert body["rejection"]["reviewed"] == 0
        assert body["rejection"]["rate"] is None

    @pytest.mark.asyncio
    async def test_status_works_without_run_store(self, client):
        """run_store 없어도 /status 는 200 반환 (UI 가 disabled 안내를 그리도록)."""
        resp = await client.get(
            "/admin/v1/memory/dreaming/status", headers=HEADERS
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["metrics_enabled"] is False
        assert body["last_run"] is None
        assert body["kpi_7d"] is None
        # provider 도 없으면 next_run 도 None.
        assert body["next_run"] is None

    @pytest.mark.asyncio
    async def test_status_includes_rejection_rate_from_suggestions(
        self, tmp_state, tmp_path, aiohttp_client
    ):
        """suggestion_store 가 있으면 rejection rate 가 누적 데이터로 계산되어야 한다."""
        from simpleclaw.memory.dreaming_runs import DreamingRunStore
        from simpleclaw.memory.insights import InsightMeta
        from simpleclaw.memory.suggestions import SuggestionStore

        sugg_store = SuggestionStore(tmp_path / "sugg.jsonl")
        # 3건 적재 — accepted 1, rejected 2 → reviewed=3, rejected=2, rate≈0.667.
        for i, status in enumerate(("accepted", "rejected", "rejected")):
            m = InsightMeta(
                topic=f"topic{i}",
                text=f"text{i}",
                evidence_count=1,
                confidence=0.4,
                source_msg_ids=[],
            )
            s = sugg_store.upsert_pending(m)
            sugg_store.update_status(s.id, status)

        run_store = DreamingRunStore(tmp_path / "runs.jsonl")
        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            dreaming_run_store=run_store,
            suggestion_store=sugg_store,
        )
        c = await aiohttp_client(srv.get_app())
        resp = await c.get("/admin/v1/memory/dreaming/status", headers=HEADERS)
        assert resp.status == 200
        body = await resp.json()
        assert body["rejection"]["reviewed"] == 3
        assert body["rejection"]["rejected"] == 2
        assert abs(body["rejection"]["rate"] - 2 / 3) < 1e-6

    @pytest.mark.asyncio
    async def test_status_provider_exception_does_not_500(
        self, tmp_state, tmp_path, aiohttp_client
    ):
        """provider 가 예외를 던져도 /status 응답은 200 — KPI/last_run 정상 노출."""
        from simpleclaw.memory.dreaming_runs import DreamingRunStore

        run_store = DreamingRunStore(tmp_path / "runs.jsonl")

        def boom() -> dict:
            raise RuntimeError("provider down")

        srv = AdminAPIServer(
            auth_token="test-token",
            config_path=tmp_state["config_path"],
            audit_log=AuditLog(tmp_state["audit_dir"]),
            secrets_manager=_make_secrets_manager(),
            admin_state_dir=tmp_state["state_dir"],
            dreaming_run_store=run_store,
            dreaming_status_provider=boom,
        )
        c = await aiohttp_client(srv.get_app())
        resp = await c.get("/admin/v1/memory/dreaming/status", headers=HEADERS)
        assert resp.status == 200
        body = await resp.json()
        assert body["metrics_enabled"] is True
        assert body["next_run"] is None
        assert body["trigger_blockers"] == []

