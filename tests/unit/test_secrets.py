"""simpleclaw.security.secrets 모듈의 단위 테스트.

세 가지 백엔드(env/keyring/file), 참조 파서, 매니저 라우팅, 그리고 config 로더와의
통합을 검증한다. keyring 백엔드는 실제 OS 자격 증명 저장소를 건드리지 않도록
fake 모듈로 patch한다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from simpleclaw.security import secrets
from simpleclaw.security.secrets import (
    EncryptedFileBackend,
    EnvBackend,
    KeyringBackend,
    SecretReference,
    SecretsError,
    SecretsManager,
    default_manager,
    resolve_secret,
    set_default_manager,
)


# ---------------------------------------------------------------------------
# 참조 파서
# ---------------------------------------------------------------------------


class TestSecretReference:
    """``"scheme:name"`` 문법 파서가 알려진 스킴만 인식하는지 검증."""

    @pytest.mark.parametrize(
        "value,scheme,name",
        [
            ("env:OPENAI_API_KEY", "env", "OPENAI_API_KEY"),
            ("keyring:claude", "keyring", "claude"),
            ("file:webhook", "file", "webhook"),
            ("plain:literal-value", "plain", "literal-value"),
            # 스킴 대소문자 무시 — 사용자 오타 허용.
            ("ENV:HOME", "env", "HOME"),
        ],
    )
    def test_parses_known_schemes(self, value, scheme, name):
        ref = SecretReference.parse(value)
        assert ref is not None
        assert ref.scheme == scheme
        assert ref.name == name

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "no-colon-here",
            "sk-ant-api01-realkey",  # 평문 API 키
            "https://example.com",  # 알려지지 않은 스킴
            "vault:secret",
            None,
        ],
    )
    def test_unrecognized_returns_none(self, value):
        # 평문 API 키가 우연히 콜론을 포함해도 알려진 스킴이 아니면 None.
        assert SecretReference.parse(value) is None  # type: ignore[arg-type]

    def test_value_with_colon_in_name(self):
        """name 부분이 추가 콜론을 포함해도 첫 콜론까지만 스킴으로 파싱."""
        ref = SecretReference.parse("env:NESTED:VAR:NAME")
        assert ref is not None
        assert ref.scheme == "env"
        assert ref.name == "NESTED:VAR:NAME"


# ---------------------------------------------------------------------------
# 환경변수 백엔드
# ---------------------------------------------------------------------------


class TestEnvBackend:
    def test_get_existing(self, monkeypatch):
        monkeypatch.setenv("SIMPLECLAW_TEST_ENV", "value-1")
        assert EnvBackend().get("SIMPLECLAW_TEST_ENV") == "value-1"

    def test_get_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("SIMPLECLAW_NOT_SET", raising=False)
        assert EnvBackend().get("SIMPLECLAW_NOT_SET") is None

    def test_set_and_delete(self, monkeypatch):
        monkeypatch.delenv("SIMPLECLAW_TEMP", raising=False)
        backend = EnvBackend()
        backend.set("SIMPLECLAW_TEMP", "v")
        assert backend.get("SIMPLECLAW_TEMP") == "v"
        backend.delete("SIMPLECLAW_TEMP")
        assert backend.get("SIMPLECLAW_TEMP") is None


# ---------------------------------------------------------------------------
# Keyring 백엔드 — fake 모듈로 OS 자격 증명 저장소를 격리
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """테스트용 in-memory keyring."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.fail_get = False
        self.fail_set = False

    def get_password(self, service, key):
        if self.fail_get:
            raise RuntimeError("simulated keyring failure")
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        if self.fail_set:
            raise RuntimeError("simulated keyring write failure")
        self.store[(service, key)] = value

    def delete_password(self, service, key):
        del self.store[(service, key)]


@pytest.fixture
def fake_keyring(monkeypatch):
    """전역 keyring 모듈을 fake로 교체하고 fake 인스턴스를 돌려준다."""
    fake = _FakeKeyring()
    fake_module = types.SimpleNamespace(
        get_password=fake.get_password,
        set_password=fake.set_password,
        delete_password=fake.delete_password,
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_module)
    return fake


class TestKeyringBackend:
    def test_set_and_get(self, fake_keyring):
        backend = KeyringBackend(service="simpleclaw-test")
        backend.set("anthropic", "sk-ant-1234")
        assert backend.get("anthropic") == "sk-ant-1234"
        assert fake_keyring.store == {("simpleclaw-test", "anthropic"): "sk-ant-1234"}

    def test_missing_returns_none(self, fake_keyring):
        backend = KeyringBackend()
        assert backend.get("nope") is None

    def test_get_failure_returns_none(self, fake_keyring):
        """헤드리스 keyring 등 OS 예외는 ``None``으로 변환되어 폴백을 허용한다."""
        fake_keyring.fail_get = True
        backend = KeyringBackend()
        assert backend.get("anything") is None

    def test_set_failure_raises(self, fake_keyring):
        fake_keyring.fail_set = True
        backend = KeyringBackend()
        with pytest.raises(SecretsError):
            backend.set("k", "v")


# ---------------------------------------------------------------------------
# 암호화 파일 백엔드
# ---------------------------------------------------------------------------


class TestEncryptedFileBackend:
    def test_round_trip(self, tmp_path: Path):
        """저장한 시크릿이 동일 키로 복호화되는지 확인."""
        backend = EncryptedFileBackend(
            vault_path=tmp_path / "vault.enc",
            master_key_path=tmp_path / "master.key",
        )
        backend.set("openai", "sk-secret")
        assert backend.get("openai") == "sk-secret"

    def test_master_key_persists(self, tmp_path: Path):
        """첫 호출에서 자동 생성된 마스터 키가 두 번째 인스턴스에서도 그대로 동작."""
        vault = tmp_path / "v.enc"
        key = tmp_path / "m.key"
        EncryptedFileBackend(vault_path=vault, master_key_path=key).set("k", "v")
        # 새 인스턴스 — 같은 파일을 읽어서 복호화해야 함.
        out = EncryptedFileBackend(vault_path=vault, master_key_path=key).get("k")
        assert out == "v"

    def test_master_key_from_env(self, tmp_path: Path, monkeypatch):
        """``SIMPLECLAW_MASTER_KEY`` 환경변수가 우선 적용된다."""
        from cryptography.fernet import Fernet

        master = Fernet.generate_key()
        monkeypatch.setenv(secrets.MASTER_KEY_ENV, master.decode())
        # 파일 경로는 의도적으로 잘못 설정 — env가 이기는지 확인.
        backend = EncryptedFileBackend(
            vault_path=tmp_path / "v.enc",
            master_key_path=tmp_path / "nonexistent" / "m.key",
        )
        backend.set("k", "v")
        assert backend.get("k") == "v"
        # env에 없는 상태로 다시 시도하면 파일에서 읽지 못하므로 실패해야 함.
        monkeypatch.delenv(secrets.MASTER_KEY_ENV)
        with pytest.raises(Exception):
            EncryptedFileBackend(
                vault_path=tmp_path / "v.enc",
                master_key_path=tmp_path / "nonexistent" / "m.key",
            ).get("k")

    def test_missing_returns_none(self, tmp_path: Path):
        backend = EncryptedFileBackend(
            vault_path=tmp_path / "v.enc",
            master_key_path=tmp_path / "m.key",
        )
        assert backend.get("never-stored") is None

    def test_delete(self, tmp_path: Path):
        backend = EncryptedFileBackend(
            vault_path=tmp_path / "v.enc",
            master_key_path=tmp_path / "m.key",
        )
        backend.set("k", "v")
        backend.delete("k")
        assert backend.get("k") is None

    def test_wrong_master_key_raises(self, tmp_path: Path):
        """다른 마스터 키로 열면 복호화 실패를 명확히 알린다."""
        vault = tmp_path / "v.enc"
        EncryptedFileBackend(
            vault_path=vault,
            master_key_path=tmp_path / "m1.key",
        ).set("k", "v")
        with pytest.raises(SecretsError):
            EncryptedFileBackend(
                vault_path=vault,
                master_key_path=tmp_path / "m2.key",
            ).get("k")

    def test_master_key_file_is_0600(self, tmp_path: Path):
        """자동 생성된 마스터 키 파일은 0600 권한이어야 한다."""
        if sys.platform == "win32":
            pytest.skip("Windows는 POSIX 권한 검사 비대상")
        import stat as stat_mod

        key_path = tmp_path / "m.key"
        EncryptedFileBackend(
            vault_path=tmp_path / "v.enc",
            master_key_path=key_path,
        ).set("k", "v")
        mode = stat_mod.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# SecretsManager 라우팅
# ---------------------------------------------------------------------------


class TestSecretsManager:
    def test_resolve_plain_string_legacy(self):
        """접두어 없는 평문은 그대로 반환 — 하위 호환."""
        m = SecretsManager(backends={})
        assert m.resolve("sk-test-key") == "sk-test-key"

    def test_resolve_plain_scheme(self):
        m = SecretsManager(backends={})
        assert m.resolve("plain:literal-secret") == "literal-secret"

    def test_resolve_env_routes_to_env_backend(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "v1")
        m = SecretsManager(backends={"env": EnvBackend()})
        assert m.resolve("env:MY_KEY") == "v1"

    def test_resolve_missing_returns_empty(self, monkeypatch):
        """백엔드가 ``None``을 돌려주면 빈 문자열 — 호출자는 키 미설정으로 인식."""
        monkeypatch.delenv("ABSENT", raising=False)
        m = SecretsManager(backends={"env": EnvBackend()})
        assert m.resolve("env:ABSENT") == ""

    def test_resolve_unknown_scheme_returns_empty(self, caplog):
        # 'env'만 등록된 매니저에 'keyring' 참조 — 백엔드 누락 경고 후 빈 문자열.
        m = SecretsManager(backends={"env": EnvBackend()})
        out = m.resolve("keyring:something")
        assert out == ""

    def test_resolve_empty_input(self):
        m = SecretsManager(backends={})
        assert m.resolve("") == ""
        assert m.resolve(None) == ""

    def test_store_and_resolve_keyring(self, fake_keyring):
        m = SecretsManager(
            backends={
                "env": EnvBackend(),
                "keyring": KeyringBackend(),
            }
        )
        m.store("keyring", "anthropic", "sk-ant-1")
        assert m.resolve("keyring:anthropic") == "sk-ant-1"

    def test_store_plain_is_invalid(self):
        m = SecretsManager(backends={})
        with pytest.raises(SecretsError):
            m.store("plain", "k", "v")


class TestDefaultManager:
    """모듈 레벨 기본 매니저는 프로세스 전역 캐시여야 한다."""

    def teardown_method(self):
        set_default_manager(None)

    def test_default_manager_is_cached(self):
        a = default_manager()
        b = default_manager()
        assert a is b

    def test_set_default_manager_overrides(self, monkeypatch):
        monkeypatch.setenv("OVERRIDE_KEY", "from-test")
        custom = SecretsManager(backends={"env": EnvBackend()})
        set_default_manager(custom)
        assert resolve_secret("env:OVERRIDE_KEY") == "from-test"


# ---------------------------------------------------------------------------
# config 로더 통합 — 평문 회귀 방지 + 참조 해소가 정상 동작하는지 확인
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    """``load_*_config()``가 시크릿 참조를 자동 해소하는지 확인한다."""

    @pytest.fixture(autouse=True)
    def _isolate_default_manager(self):
        # 각 테스트가 독립된 매니저를 쓰도록 격리.
        yield
        set_default_manager(None)

    def test_llm_config_resolves_env_reference(self, tmp_path: Path, monkeypatch):
        from simpleclaw.config import load_llm_config

        monkeypatch.setenv("CLAUDE_KEY_TEST", "sk-ant-resolved")
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            'llm:\n  providers:\n    claude:\n      api_key: "env:CLAUDE_KEY_TEST"\n',
            encoding="utf-8",
        )
        result = load_llm_config(cfg)
        assert result["providers"]["claude"]["api_key"] == "sk-ant-resolved"

    def test_llm_config_plain_value_still_works(self, tmp_path: Path):
        """레거시 평문 api_key는 변경 없이 그대로 통과해야 한다 (회귀 방지)."""
        from simpleclaw.config import load_llm_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "llm:\n  providers:\n    openai:\n      api_key: sk-test-key\n",
            encoding="utf-8",
        )
        result = load_llm_config(cfg)
        assert result["providers"]["openai"]["api_key"] == "sk-test-key"

    def test_telegram_config_resolves_keyring_reference(
        self, tmp_path: Path, fake_keyring
    ):
        from simpleclaw.config import load_telegram_config

        # 기본 매니저를 fake keyring을 가진 인스턴스로 교체.
        manager = SecretsManager(
            backends={"env": EnvBackend(), "keyring": KeyringBackend()}
        )
        manager.store("keyring", "tg_token", "123:ABC-from-keyring")
        set_default_manager(manager)

        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            'telegram:\n  bot_token: "keyring:tg_token"\n  whitelist:\n    user_ids: [42]\n',
            encoding="utf-8",
        )
        result = load_telegram_config(cfg)
        assert result["bot_token"] == "123:ABC-from-keyring"
        assert result["whitelist"]["user_ids"] == [42]

    def test_webhook_config_resolves_file_reference(self, tmp_path: Path):
        from simpleclaw.config import load_webhook_config

        manager = SecretsManager(
            backends={
                "env": EnvBackend(),
                "file": EncryptedFileBackend(
                    vault_path=tmp_path / "v.enc",
                    master_key_path=tmp_path / "m.key",
                ),
            }
        )
        manager.store("file", "webhook_token", "wh-secret")
        set_default_manager(manager)

        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            'webhook:\n  enabled: true\n  auth_token: "file:webhook_token"\n',
            encoding="utf-8",
        )
        result = load_webhook_config(cfg)
        assert result["auth_token"] == "wh-secret"

    def test_missing_reference_yields_empty_string(self, tmp_path: Path, monkeypatch):
        """참조 대상이 없으면 빈 문자열로 폴백해야 한다 (앱이 죽지 않도록)."""
        from simpleclaw.config import load_llm_config

        monkeypatch.delenv("ABSENT_KEY_SC", raising=False)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            'llm:\n  providers:\n    claude:\n      api_key: "env:ABSENT_KEY_SC"\n',
            encoding="utf-8",
        )
        result = load_llm_config(cfg)
        assert result["providers"]["claude"]["api_key"] == ""
