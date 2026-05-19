"""``scripts/setup_admin_api.py`` 단위 테스트 (BIZ-245).

검증 항목:

- ``_resolve_token_backend`` 가 config.yaml 의 ``admin_api.token_secret`` 참조를
  올바른 (backend, key) 쌍으로 해석한다 — keyring/file/env/plain/없음.
- ``ensure_token`` 이 지정한 백엔드에 발급/재사용 결과를 일관되게 반환한다.
- ``main()`` 의 end-to-end 흐름: 토큰 발급 → ``.env.local`` 동기화 → ``config.yaml``
  보강. BIZ-244 사고 시나리오(.env.local 만 stale) 가 재현되지 않는다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from simpleclaw.security.secrets import SecretsError, SecretsManager, set_default_manager


# scripts/setup_admin_api.py 는 패키지가 아니므로 importlib 로 직접 로드한다.
# Pytest 가 ``pythonpath = ['src']`` 만 보장하므로 scripts 경로는 수동 처리.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup_admin_api.py"


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("setup_admin_api", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_admin_api"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup_mod():
    return _load_setup_module()


class _InMemoryBackend:
    """단위 테스트용 시크릿 백엔드 — keyring/keychain 을 건드리지 않는다."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


@pytest.fixture
def in_memory_manager(monkeypatch):
    """모든 백엔드를 인메모리로 교체한 매니저를 모듈 전역 SoT 에도 꽂는다."""
    keyring_be = _InMemoryBackend("keyring")
    file_be = _InMemoryBackend("file")
    env_be = _InMemoryBackend("env")
    manager = SecretsManager(
        backends={"env": env_be, "keyring": keyring_be, "file": file_be}
    )
    set_default_manager(manager)
    # SecretsManager() 새 인스턴스도 같은 인메모리 backends 를 보도록 패치한다.
    from simpleclaw.security import secrets as secrets_mod

    monkeypatch.setattr(
        secrets_mod, "SecretsManager", lambda *a, **kw: manager, raising=True
    )
    yield manager
    set_default_manager(None)


class TestResolveTokenBackend:
    def test_returns_file_backend_when_config_says_file(self, tmp_path, setup_mod):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump({"admin_api": {"token_secret": "file:admin_api_token"}}),
            encoding="utf-8",
        )
        backend, key = setup_mod._resolve_token_backend(config)
        assert (backend, key) == ("file", "admin_api_token")

    def test_returns_keyring_when_config_says_keyring(self, tmp_path, setup_mod):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump({"admin_api": {"token_secret": "keyring:admin_api_token"}}),
            encoding="utf-8",
        )
        backend, key = setup_mod._resolve_token_backend(config)
        assert (backend, key) == ("keyring", "admin_api_token")

    def test_falls_back_when_config_missing(self, tmp_path, setup_mod):
        backend, key = setup_mod._resolve_token_backend(tmp_path / "missing.yaml")
        assert (backend, key) == ("keyring", "admin_api_token")

    def test_falls_back_for_plain_reference(self, tmp_path, setup_mod, capsys):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump({"admin_api": {"token_secret": "plain:literal-value"}}),
            encoding="utf-8",
        )
        backend, key = setup_mod._resolve_token_backend(config)
        # ``plain:`` 은 회전 의미가 없으므로 폴백 + 운영자 안내(키 이름은 fallback default).
        assert (backend, key) == ("keyring", "admin_api_token")

    def test_warns_and_falls_back_for_env_reference(self, tmp_path, setup_mod, capsys):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump(
                {"admin_api": {"token_secret": "env:ADMIN_API_TOKEN_RAW"}}
            ),
            encoding="utf-8",
        )
        backend, key = setup_mod._resolve_token_backend(config)
        # env 는 영속성이 없어 본 스크립트는 회전하지 않는다 — 폴백 + 경고.
        assert backend == "keyring"
        assert key == "ADMIN_API_TOKEN_RAW"
        out = capsys.readouterr().out
        assert "env:ADMIN_API_TOKEN_RAW" in out
        assert "keyring/file" in out


class TestEnsureToken:
    def test_reuses_existing_token(self, in_memory_manager, setup_mod):
        in_memory_manager.get_backend("file").set("admin_api_token", "existing")
        token, created = setup_mod.ensure_token(backend="file")
        assert (token, created) == ("existing", False)

    def test_creates_new_token_when_missing(self, in_memory_manager, setup_mod):
        token, created = setup_mod.ensure_token(backend="file")
        assert created is True
        assert isinstance(token, str) and len(token) > 32
        assert in_memory_manager.get_backend("file").get("admin_api_token") == token

    def test_force_overwrites_existing(self, in_memory_manager, setup_mod):
        in_memory_manager.get_backend("file").set("admin_api_token", "stale")
        token, created = setup_mod.ensure_token(force=True, backend="file")
        assert created is True
        assert token != "stale"

    def test_writes_to_configured_backend_only(self, in_memory_manager, setup_mod):
        # BIZ-244 회귀 가드 — 운영자가 file 백엔드로 운용 중일 때 스크립트가 keyring 에만
        # 발급하면 데몬은 옛 file 값을 계속 읽어 모든 admin 호출이 401 이 된다.
        setup_mod.ensure_token(backend="file")
        assert in_memory_manager.get_backend("file").get("admin_api_token") is not None
        assert in_memory_manager.get_backend("keyring").get("admin_api_token") is None


class TestMainFlow:
    def test_end_to_end_creates_env_local_and_token(
        self, tmp_path, in_memory_manager, setup_mod, monkeypatch
    ):
        # 신선 셋업 — config.yaml 도 없고 .env.local 도 없는 상태.
        config = tmp_path / "config.yaml"
        env_local = tmp_path / "web" / "admin" / ".env.local"

        # config.yaml.example 도 없으면 main() 이 기본값 yaml 을 만든다 — 본 경로 가드.
        rc = setup_mod.main([
            "--config",
            str(config),
            "--env-local",
            str(env_local),
        ])

        assert rc == 0
        assert config.is_file()
        assert env_local.is_file()
        text = env_local.read_text(encoding="utf-8")
        # 발급된 토큰이 ``.env.local`` 에 들어가 있고, 키링에도 같은 값이 있어야 한다.
        token = in_memory_manager.get_backend("keyring").get("admin_api_token")
        assert token is not None and f"ADMIN_API_TOKEN={token}" in text

    def test_biz_244_scenario_env_local_stale_is_resynced(
        self, tmp_path, in_memory_manager, setup_mod
    ):
        # BIZ-244 재현 시나리오 — file 백엔드에 새 토큰이 있고 ``.env.local`` 은 옛 값을 갖고
        # 있다. ``setup_admin_api.py`` 를 실행하면 ``.env.local`` 이 file 백엔드 값으로
        # 다시 정합화돼야 한다.
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump({"admin_api": {"token_secret": "file:admin_api_token"}}),
            encoding="utf-8",
        )
        in_memory_manager.get_backend("file").set("admin_api_token", "fresh-vault-value")

        env_local = tmp_path / ".env.local"
        env_local.write_text("ADMIN_API_TOKEN=stale-old-value\n", encoding="utf-8")

        rc = setup_mod.main([
            "--config",
            str(config),
            "--env-local",
            str(env_local),
        ])

        assert rc == 0
        text = env_local.read_text(encoding="utf-8")
        assert "ADMIN_API_TOKEN=fresh-vault-value" in text
        assert "stale-old-value" not in text

    def test_force_rotates_token_in_configured_backend(
        self, tmp_path, in_memory_manager, setup_mod
    ):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump({"admin_api": {"token_secret": "file:admin_api_token"}}),
            encoding="utf-8",
        )
        in_memory_manager.get_backend("file").set("admin_api_token", "old")

        env_local = tmp_path / ".env.local"

        rc = setup_mod.main([
            "--config",
            str(config),
            "--env-local",
            str(env_local),
            "--force",
        ])

        assert rc == 0
        new_token = in_memory_manager.get_backend("file").get("admin_api_token")
        assert new_token is not None and new_token != "old"
        # ``.env.local`` 도 새 토큰으로 동기화된다.
        assert f"ADMIN_API_TOKEN={new_token}" in env_local.read_text(encoding="utf-8")

    def test_print_token_does_not_modify_env_local(
        self, tmp_path, in_memory_manager, setup_mod, capsys
    ):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.safe_dump({"admin_api": {"token_secret": "keyring:admin_api_token"}}),
            encoding="utf-8",
        )
        env_local = tmp_path / ".env.local"

        rc = setup_mod.main([
            "--config",
            str(config),
            "--env-local",
            str(env_local),
            "--print-token",
        ])

        assert rc == 0
        captured = capsys.readouterr()
        printed = captured.out.strip()
        assert printed
        # ``--print-token`` 은 부수 효과 없이 토큰만 출력한다.
        assert not env_local.exists()

    def test_returns_nonzero_when_backend_raises(
        self, tmp_path, in_memory_manager, setup_mod, monkeypatch
    ):
        # 백엔드 저장 실패는 ``--force`` 또는 신규 발급 시점에 발생할 수 있다 — main 이
        # 비-0 종료 코드를 반환해야 한다.
        def boom(self, key, value):
            raise SecretsError("backend offline")

        monkeypatch.setattr(_InMemoryBackend, "set", boom, raising=False)

        rc = setup_mod.main([
            "--config",
            str(tmp_path / "missing.yaml"),
            "--env-local",
            str(tmp_path / ".env.local"),
        ])
        assert rc == 1
