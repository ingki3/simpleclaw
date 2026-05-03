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
