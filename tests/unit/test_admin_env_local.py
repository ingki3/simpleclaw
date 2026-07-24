"""``simpleclaw.channels.admin_env_local`` 단위 테스트 (BIZ-245).

BIZ-244 사고 — vault 만 회전되고 ``web/admin/.env.local`` 이 stale 인 상태로 방치돼
모든 admin UI 패널이 401 로 빈 상태가 됐다 — 의 사후 재발 방지 로직.

검증 항목:

- 새 파일 생성, 기존 파일 갱신, 토큰만 라인 교체, 주석/빈 줄 보존, idempotency.
- ``make_secret_rotation_callback`` 이 ``admin_api_token`` 회전에만 반응하고,
  디스크 오류는 회전 자체를 막지 않는다.
"""

from __future__ import annotations

import logging

from simpleclaw.channels.admin_env_local import (
    BASE_KEY,
    DEFAULT_ADMIN_BASE,
    TOKEN_KEY,
    make_secret_rotation_callback,
    sync_env_local,
)


class TestSyncEnvLocal:
    def test_creates_new_file_with_token_and_base(self, tmp_path):
        env_path = tmp_path / ".env.local"

        changed = sync_env_local("new-token", env_path=env_path)

        assert changed is True
        content = env_path.read_text(encoding="utf-8")
        assert f"{TOKEN_KEY}=new-token" in content
        assert f"{BASE_KEY}={DEFAULT_ADMIN_BASE}" in content
        # 신선 생성은 마지막 개행으로 끝나야 한다 (dotenv 관례).
        assert content.endswith("\n")

    def test_replaces_only_token_line_and_preserves_other_keys(self, tmp_path):
        env_path = tmp_path / ".env.local"
        env_path.write_text(
            "# 운영자 메모\n"
            "PORT=8200\n"
            f"{TOKEN_KEY}=old-token\n"
            f"{BASE_KEY}=http://127.0.0.1:8082\n"
            "OTHER=keep-me\n",
            encoding="utf-8",
        )

        changed = sync_env_local("new-token", env_path=env_path)

        assert changed is True
        lines = env_path.read_text(encoding="utf-8").splitlines()
        # 라인 순서가 보존되고, TOKEN_KEY 라인만 교체됐는지 확인.
        assert lines[0] == "# 운영자 메모"
        assert lines[1] == "PORT=8200"
        assert lines[2] == f"{TOKEN_KEY}=new-token"
        assert lines[3] == f"{BASE_KEY}=http://127.0.0.1:8082"
        assert lines[4] == "OTHER=keep-me"

    def test_idempotent_when_values_match(self, tmp_path):
        env_path = tmp_path / ".env.local"
        env_path.write_text(
            f"{TOKEN_KEY}=same-token\n{BASE_KEY}={DEFAULT_ADMIN_BASE}\n",
            encoding="utf-8",
        )
        first_mtime = env_path.stat().st_mtime_ns

        changed = sync_env_local("same-token", env_path=env_path)

        assert changed is False
        # 변경 없음 시 디스크 쓰기를 생략 — mtime 이 변하지 않아야 한다.
        assert env_path.stat().st_mtime_ns == first_mtime

    def test_seeds_from_example_when_env_local_missing(self, tmp_path):
        env_path = tmp_path / ".env.local"
        example_path = tmp_path / ".env.local.example"
        example_path.write_text(
            "# 운영자 메모\n# 토큰을 채워넣으세요\nADMIN_API_TOKEN=\nADMIN_API_BASE=http://127.0.0.1:8082\n",
            encoding="utf-8",
        )

        changed = sync_env_local(
            "fresh-token", env_path=env_path, example_path=example_path
        )

        assert changed is True
        text = env_path.read_text(encoding="utf-8")
        assert "# 운영자 메모" in text  # 주석 보존
        assert f"{TOKEN_KEY}=fresh-token" in text

    def test_appends_missing_keys(self, tmp_path):
        # ``.env.local`` 이 ``ADMIN_API_TOKEN`` 만 있고 ``ADMIN_API_BASE`` 가 없는 경우,
        # 파일 끝에 누락 키를 추가한다.
        env_path = tmp_path / ".env.local"
        env_path.write_text("ADMIN_API_TOKEN=tok\n", encoding="utf-8")

        sync_env_local("tok", env_path=env_path)

        content = env_path.read_text(encoding="utf-8")
        assert f"{BASE_KEY}={DEFAULT_ADMIN_BASE}" in content


class TestSecretRotationCallback:
    def test_callback_syncs_env_local_for_admin_api_token(self, tmp_path):
        env_path = tmp_path / ".env.local"
        env_path.write_text(f"{TOKEN_KEY}=old\n", encoding="utf-8")

        cb = make_secret_rotation_callback(env_path=env_path)
        cb("file", "admin_api_token", "new-token")

        text = env_path.read_text(encoding="utf-8")
        assert f"{TOKEN_KEY}=new-token" in text

    def test_callback_ignores_other_secret_names(self, tmp_path):
        # ``claude_api_key`` 회전은 ``.env.local`` 과 무관 — 디스크에 접근하지 않아야 한다.
        env_path = tmp_path / ".env.local"

        cb = make_secret_rotation_callback(env_path=env_path)
        cb("keyring", "claude_api_key", "sk-...")

        assert not env_path.exists()

    def test_callback_swallows_oserror_and_logs_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        # 디스크 쓰기 실패는 회전 자체를 막지 않아야 한다 — 본 콜백은 호출 결과를 반환하지
        # 않지만 예외를 던지지 말아야 한다.
        env_path = tmp_path / "subdir" / ".env.local"

        def boom(*args, **kwargs):
            raise OSError("disk full")

        # ``sync_env_local`` 자체를 패치해 OSError 를 발생시켜 콜백의 except 경로를 친다.
        from simpleclaw.channels import admin_env_local

        monkeypatch.setattr(admin_env_local, "sync_env_local", boom)

        cb = make_secret_rotation_callback(env_path=env_path)
        with caplog.at_level(logging.WARNING, logger="simpleclaw.channels.admin_env_local"):
            cb("file", "admin_api_token", "new-token")

        assert any(
            ".env.local 동기화 실패" in rec.message for rec in caplog.records
        )


class TestRepoRootResolution:
    def test_default_env_local_points_to_repo_web_admin(self):
        # ``DEFAULT_ENV_LOCAL_PATH`` 가 리포 트리의 ``web/admin/.env.local`` 을 가리키는지
        # 회귀 방지. 모듈을 다른 위치로 옮기면 ``parents[3]`` 계산이 깨질 수 있다.
        from simpleclaw.channels.admin_env_local import DEFAULT_ENV_LOCAL_PATH

        assert DEFAULT_ENV_LOCAL_PATH.parts[-3:] == ("web", "admin", ".env.local")
        # 리포에 실제로 example 파일이 존재해야 한다 — 시드 경로 회귀 방지.
        example = DEFAULT_ENV_LOCAL_PATH.with_name(".env.local.example")
        assert example.is_file(), "web/admin/.env.local.example 가 사라졌습니다"
