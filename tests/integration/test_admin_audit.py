"""Admin API 감사 통합 테스트 — BIZ-41.

PATCH → audit 기록 → undo 라운드트립을 실제 aiohttp 서버를 띄워 검증한다.
또한 마스터 키 회전 후 모든 ``file:`` 시크릿이 정상 read되는지 확인해
재암호화 경로(매니저 ↔ 백엔드)가 끊기지 않았는지 보증한다.
"""

from __future__ import annotations


import pytest
import yaml

from simpleclaw.channels.admin_api import AdminAPIServer, _pending_changes_path
from simpleclaw.channels.admin_audit import AuditLog
from simpleclaw.security.secrets import (
    EncryptedFileBackend,
    EnvBackend,
    MASTER_KEY_ENV,
    SecretsManager,
)


@pytest.fixture
def workspace(tmp_path):
    """초기 config.yaml + 격리된 admin/audit 디렉토리."""
    config = {
        "agent": {
            "history_limit": 20,
            "max_tool_iterations": 5,
            "db_path": ".agent/conversations.db",
            "workspace_dir": ".agent/workspace",
        },
        "memory": {
            "rag": {
                "enabled": False,
                "model": "intfloat/multilingual-e5-small",
                "top_k": 5,
                "similarity_threshold": 0.5,
            }
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(config), encoding="utf-8")
    return {
        "tmp_path": tmp_path,
        "config_path": p,
        "audit_dir": tmp_path / "audit",
        "state_dir": tmp_path / "admin",
    }


@pytest.fixture
def file_secrets_manager(tmp_path, monkeypatch):
    """마스터 키 회전 검증용 SecretsManager — file 백엔드만 테스트 디렉토리에 둔다.

    ``MASTER_KEY_ENV``는 비워둬서 파일 기반 키 경로를 강제로 타게 한다.
    """
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    vault = tmp_path / "vault.enc"
    master = tmp_path / "master.key"
    backend = EncryptedFileBackend(vault_path=vault, master_key_path=master)
    return SecretsManager(
        backends={
            "env": EnvBackend(),
            "keyring": _InMemoryBackend("keyring"),
            "file": backend,
        }
    )


class _InMemoryBackend:
    """keyring 자리에 끼워 넣을 가벼운 인메모리 더미 — 실제 OS keyring 회피용."""

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


# ---------------------------------------------------------------------------
# PATCH → audit → undo 라운드트립
# ---------------------------------------------------------------------------


class TestPatchAuditUndoRoundtrip:
    @pytest.mark.asyncio
    async def test_hot_change_round_trip(self, workspace, aiohttp_client):
        srv = AdminAPIServer(
            auth_token="rt",
            config_path=workspace["config_path"],
            audit_log=AuditLog(workspace["audit_dir"]),
            secrets_manager=SecretsManager(
                backends={
                    "env": EnvBackend(),
                    "keyring": _InMemoryBackend("keyring"),
                    "file": _InMemoryBackend("file"),
                }
            ),
            admin_state_dir=workspace["state_dir"],
        )
        client = await aiohttp_client(srv.get_app())
        H = {"Authorization": "Bearer rt"}

        # 1) 초기 history_limit=20 → 50으로 PATCH (Hot, 즉시 반영).
        resp = await client.patch(
            "/admin/v1/config/agent",
            headers=H,
            json={"history_limit": 50},
        )
        assert resp.status == 200
        body = await resp.json()
        audit_id = body["audit_id"]
        assert body["outcome"] == "applied"

        # 디스크에 반영 확인.
        on_disk = yaml.safe_load(workspace["config_path"].read_text())
        assert on_disk["agent"]["history_limit"] == 50

        # 2) 감사 로그 검색 — 원본 항목 존재.
        resp = await client.get(
            "/admin/v1/audit?action=config.update&outcome=applied",
            headers=H,
        )
        entries = (await resp.json())["entries"]
        assert any(e["id"] == audit_id for e in entries)
        original = next(e for e in entries if e["id"] == audit_id)
        assert original["before"]["history_limit"] == 20
        assert original["after"]["history_limit"] == 50

        # 3) Undo — before로 되돌리기.
        resp = await client.post(
            f"/admin/v1/audit/{audit_id}/undo", headers=H
        )
        assert resp.status == 200
        undo_body = await resp.json()
        assert undo_body["outcome"] == "applied"
        assert undo_body["audit_id"] != audit_id

        # 디스크가 원래 값으로 복귀.
        on_disk = yaml.safe_load(workspace["config_path"].read_text())
        assert on_disk["agent"]["history_limit"] == 20

        # 4) 새 audit 항목이 추가되어 이력 보존.
        resp = await client.get(
            "/admin/v1/audit?action=config.update", headers=H
        )
        entries = (await resp.json())["entries"]
        assert len(entries) >= 2  # 원본 + undo

    @pytest.mark.asyncio
    async def test_undo_pending_process_restart_change(
        self, workspace, aiohttp_client
    ):
        srv = AdminAPIServer(
            auth_token="rt",
            config_path=workspace["config_path"],
            audit_log=AuditLog(workspace["audit_dir"]),
            secrets_manager=SecretsManager(
                backends={
                    "env": EnvBackend(),
                    "keyring": _InMemoryBackend("k"),
                    "file": _InMemoryBackend("f"),
                }
            ),
            admin_state_dir=workspace["state_dir"],
        )
        client = await aiohttp_client(srv.get_app())
        H = {"Authorization": "Bearer rt"}

        # Process-restart 정책 — pending 처리.
        resp = await client.patch(
            "/admin/v1/config/agent",
            headers=H,
            json={"db_path": "/tmp/new.db"},
        )
        body = await resp.json()
        assert body["outcome"] == "pending"
        audit_id = body["audit_id"]

        # 펜딩 파일이 생성됐는지.
        pp = _pending_changes_path(workspace["state_dir"])
        assert pp.is_file()

        # Undo — yaml은 그대로(애초에 yaml에 적용 안 됐음)이지만 새 audit가 생긴다.
        resp = await client.post(
            f"/admin/v1/audit/{audit_id}/undo", headers=H
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_validation_failure_records_rejected_audit(
        self, workspace, aiohttp_client
    ):
        srv = AdminAPIServer(
            auth_token="rt",
            config_path=workspace["config_path"],
            audit_log=AuditLog(workspace["audit_dir"]),
            secrets_manager=SecretsManager(
                backends={
                    "env": EnvBackend(),
                    "keyring": _InMemoryBackend("k"),
                    "file": _InMemoryBackend("f"),
                }
            ),
            admin_state_dir=workspace["state_dir"],
        )
        client = await aiohttp_client(srv.get_app())
        H = {"Authorization": "Bearer rt"}

        resp = await client.patch(
            "/admin/v1/config/agent",
            headers=H,
            json={"history_limit": 99999},
        )
        assert resp.status == 422

        # rejected outcome 항목이 audit에 남아 있다.
        resp = await client.get(
            "/admin/v1/audit?outcome=rejected", headers=H
        )
        entries = (await resp.json())["entries"]
        assert entries
        assert entries[0]["outcome"] == "rejected"


# ---------------------------------------------------------------------------
# 마스터 키 회전 — 재암호화 후에도 모든 file 시크릿 read 가능
# ---------------------------------------------------------------------------


class TestMasterKeyRotation:
    @pytest.mark.asyncio
    async def test_master_rotation_reencrypts_all_file_secrets(
        self, workspace, file_secrets_manager, aiohttp_client
    ):
        # 1) file 백엔드에 시크릿 3개 등록.
        plaintexts = {
            "claude_api_key": "sk-claude-001",
            "openai_api_key": "sk-openai-002",
            "webhook_auth_token": "whk-token-003",
        }
        for name, value in plaintexts.items():
            file_secrets_manager.store("file", name, value)

        # 회전 전, 모든 값이 정확히 read되는지.
        backend = file_secrets_manager.get_backend("file")
        for name, value in plaintexts.items():
            assert backend.get(name) == value

        # 2) Admin API로 마스터 키 회전 호출.
        srv = AdminAPIServer(
            auth_token="mr",
            config_path=workspace["config_path"],
            audit_log=AuditLog(workspace["audit_dir"]),
            secrets_manager=file_secrets_manager,
            admin_state_dir=workspace["state_dir"],
        )
        client = await aiohttp_client(srv.get_app())
        resp = await client.post(
            "/admin/v1/secrets/master/rotate",
            headers={"Authorization": "Bearer mr"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["outcome"] == "applied"
        assert data["reencrypted_count"] == len(plaintexts)

        # 3) 회전 후 모든 시크릿이 *동일한 평문*으로 read되어야 한다.
        for name, expected in plaintexts.items():
            assert backend.get(name) == expected

        # 4) 감사 로그에 ``secret.rotate_master`` 기록.
        entries = AuditLog(workspace["audit_dir"]).search(
            action="secret.rotate_master"
        )
        assert entries
        assert entries[0].outcome == "applied"

    @pytest.mark.asyncio
    async def test_master_rotation_with_no_file_secrets(
        self, workspace, file_secrets_manager, aiohttp_client
    ):
        # 비어 있는 볼트에서도 안전히 동작해야 한다.
        srv = AdminAPIServer(
            auth_token="mr",
            config_path=workspace["config_path"],
            audit_log=AuditLog(workspace["audit_dir"]),
            secrets_manager=file_secrets_manager,
            admin_state_dir=workspace["state_dir"],
        )
        client = await aiohttp_client(srv.get_app())
        resp = await client.post(
            "/admin/v1/secrets/master/rotate",
            headers={"Authorization": "Bearer mr"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["reencrypted_count"] == 0


# ---------------------------------------------------------------------------
# Pending → system restart 머지 통합
# ---------------------------------------------------------------------------


class TestPendingMergeOnRestart:
    @pytest.mark.asyncio
    async def test_multiple_pending_merge_into_yaml(
        self, workspace, aiohttp_client
    ):
        srv = AdminAPIServer(
            auth_token="rs",
            config_path=workspace["config_path"],
            audit_log=AuditLog(workspace["audit_dir"]),
            secrets_manager=SecretsManager(
                backends={
                    "env": EnvBackend(),
                    "keyring": _InMemoryBackend("k"),
                    "file": _InMemoryBackend("f"),
                }
            ),
            admin_state_dir=workspace["state_dir"],
        )
        client = await aiohttp_client(srv.get_app())
        H = {"Authorization": "Bearer rs"}

        # 1) 두 개의 process-restart 변경을 펜딩으로.
        await client.patch(
            "/admin/v1/config/agent",
            headers=H,
            json={"db_path": "/new/db"},
        )
        await client.patch(
            "/admin/v1/config/agent",
            headers=H,
            json={"workspace_dir": "/new/ws"},
        )

        # 2) 시스템 재시작 → 머지.
        resp = await client.post(
            "/admin/v1/system/restart",
            headers=H,
            json={"reason": "integration"},
        )
        assert resp.status == 200

        on_disk = yaml.safe_load(workspace["config_path"].read_text())
        assert on_disk["agent"]["db_path"] == "/new/db"
        assert on_disk["agent"]["workspace_dir"] == "/new/ws"
