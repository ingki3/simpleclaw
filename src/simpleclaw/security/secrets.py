"""시크릿 매니저 모듈.

API 키/토큰 등 민감 정보를 평문 ``config.yaml``이나 ``.env`` 대신 OS 자격 증명
저장소(macOS Keychain, Linux Secret Service)나 암호화 파일에 보관하기 위한
추상화 레이어를 제공한다.

설계 결정:

- **참조 문법**: 설정값에 ``"env:NAME"``, ``"keyring:NAME"``, ``"file:NAME"``,
  ``"plain:VALUE"`` 형태의 접두어가 있으면 해당 백엔드에서 시크릿을 해소한다.
  접두어가 없는 문자열은 레거시 평문 값으로 취급되어 그대로 반환된다 — 기존
  config.yaml과의 하위 호환성을 위함.
- **백엔드 우선순위는 사용자가 결정**: 매니저는 단일 분배기 역할만 하고,
  어떤 백엔드를 쓸지는 참조 문자열의 스킴이 명시한다. (전역 폴백 체인 대신
  명시적 라우팅 — 예측 가능성과 운영 디버깅 편의를 위해.)
- **외부 의존성은 지연 임포트**: ``keyring``과 ``cryptography``는 백엔드가 실제
  사용될 때 임포트한다. 환경변수만 쓰는 사용자는 추가 패키지 없이 동작.
- **마스터 키 위치**: ``SIMPLECLAW_MASTER_KEY`` 환경변수가 가장 우선,
  다음으로 ``~/.simpleclaw/master.key`` 파일. 자동 생성 시 0600 권한.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# 참조 문자열에서 인식되는 스킴
_SCHEMES = ("env", "keyring", "file", "plain")

# OS 자격 증명 저장소(keyring) 사용 시 서비스 이름 — 같은 머신에서 여러 앱이
# 충돌하지 않도록 고정 prefix를 사용한다.
KEYRING_SERVICE = "simpleclaw"

# 암호화 파일 백엔드 기본 경로 — 사용자 홈 하위에 모아두어 백업/이전이 쉽도록.
DEFAULT_VAULT_DIR = Path.home() / ".simpleclaw"
DEFAULT_VAULT_FILE = DEFAULT_VAULT_DIR / "secrets.enc"
DEFAULT_MASTER_KEY_FILE = DEFAULT_VAULT_DIR / "master.key"

# 마스터 키 환경변수 이름 — CI/도커 환경에서 파일 없이도 운영 가능.
MASTER_KEY_ENV = "SIMPLECLAW_MASTER_KEY"


class SecretsError(Exception):
    """시크릿 해소/저장 실패를 알리는 공용 예외."""


# ---------------------------------------------------------------------------
# 참조 파서
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretReference:
    """``"scheme:name"`` 형태의 시크릿 참조 표현.

    Attributes:
        scheme: 백엔드 식별자 (env / keyring / file / plain).
        name: 백엔드 내 키 이름 또는 평문 값(plain일 때).
    """

    scheme: str
    name: str

    @classmethod
    def parse(cls, value: str) -> "SecretReference | None":
        """문자열에서 참조를 파싱한다.

        스킴 접두어가 없거나 지원하지 않는 스킴이면 ``None``을 반환한다 —
        호출자는 ``None``을 받으면 해당 값을 평문으로 취급해야 한다.
        """
        if not isinstance(value, str) or ":" not in value:
            return None
        head, _, rest = value.partition(":")
        scheme = head.strip().lower()
        if scheme not in _SCHEMES:
            return None
        return cls(scheme=scheme, name=rest)


# ---------------------------------------------------------------------------
# 백엔드 인터페이스
# ---------------------------------------------------------------------------


class SecretBackend(Protocol):
    """시크릿 백엔드 공통 인터페이스.

    구현체는 ``get``/``set``/``delete``를 제공해야 한다. 시크릿이 없으면 ``get``은
    ``None``을 반환한다(예외 대신).
    """

    name: str

    def get(self, key: str) -> str | None:  # pragma: no cover - 인터페이스
        ...

    def set(self, key: str, value: str) -> None:  # pragma: no cover - 인터페이스
        ...

    def delete(self, key: str) -> None:  # pragma: no cover - 인터페이스
        ...


# ---------------------------------------------------------------------------
# 환경변수 백엔드
# ---------------------------------------------------------------------------


class EnvBackend:
    """OS 환경변수에서 시크릿을 읽는 백엔드.

    ``set``/``delete``는 현재 프로세스의 환경변수를 변경할 뿐 영구 저장이
    아니므로, 운영 시에는 ``keyring``이나 ``file`` 백엔드를 권장한다.
    """

    name = "env"

    def get(self, key: str) -> str | None:
        return os.environ.get(key)

    def set(self, key: str, value: str) -> None:
        # 환경변수 백엔드는 영속성이 없으므로 set은 현재 프로세스에만 반영된다.
        os.environ[key] = value

    def delete(self, key: str) -> None:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# OS keyring 백엔드 (macOS Keychain / Linux Secret Service)
# ---------------------------------------------------------------------------


class KeyringBackend:
    """OS 자격 증명 저장소를 활용하는 백엔드.

    macOS의 Keychain, Linux의 Secret Service(예: gnome-keyring/KWallet),
    Windows Credential Manager가 자동 선택된다. 백엔드가 없거나 잠긴 환경(헤드리스
    Linux 등)에서는 ``get`` 시 ``None``, ``set`` 시 ``SecretsError``를 반환한다 —
    호출자가 폴백을 결정할 수 있도록.
    """

    name = "keyring"

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self._service = service

    def _module(self):
        # 지연 임포트 — 환경변수만 쓰는 경로는 keyring 의존성을 강제하지 않는다.
        try:
            import keyring  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - 패키지 미설치 환경
            raise SecretsError(
                "keyring 패키지가 설치되어 있지 않습니다. "
                "`pip install keyring`을 실행하거나 file/env 백엔드를 사용하세요."
            ) from exc
        return keyring

    def get(self, key: str) -> str | None:
        try:
            return self._module().get_password(self._service, key)
        except SecretsError:
            raise
        except Exception as exc:  # noqa: BLE001 — keyring은 다양한 OS 예외를 던짐
            # 헤드리스/잠긴 keyring은 운영 불능 신호 — None으로 처리해 호출자가
            # 폴백을 결정하도록 한다.
            logger.warning("keyring.get_password 실패 (%s): %s", key, exc)
            return None

    def set(self, key: str, value: str) -> None:
        try:
            self._module().set_password(self._service, key, value)
        except SecretsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SecretsError(
                f"keyring에 시크릿을 저장할 수 없습니다 ({key}): {exc}"
            ) from exc

    def delete(self, key: str) -> None:
        try:
            self._module().delete_password(self._service, key)
        except SecretsError:
            raise
        except Exception as exc:  # noqa: BLE001
            # PasswordDeleteError 등 — 이미 없을 수도 있으므로 경고만.
            logger.warning("keyring.delete_password 실패 (%s): %s", key, exc)


# ---------------------------------------------------------------------------
# 암호화 파일 백엔드 (cryptography Fernet)
# ---------------------------------------------------------------------------


class EncryptedFileBackend:
    """Fernet 대칭 암호로 보호되는 파일 기반 시크릿 저장소.

    keyring을 쓸 수 없는 환경(헤드리스 Linux, 컨테이너 등)에서 폴백으로 사용한다.
    파일 형식은 평문 JSON ``{"name": "<Fernet 토큰>"}``이며, 각 값은 마스터 키로
    개별 암호화된다. 마스터 키는 ``SIMPLECLAW_MASTER_KEY`` 환경변수가 우선,
    없으면 ``master_key_path`` 파일에서 읽거나 자동 생성된다(0600 권한).
    """

    name = "file"

    def __init__(
        self,
        vault_path: Path | None = None,
        master_key_path: Path | None = None,
    ) -> None:
        self._vault_path = Path(vault_path) if vault_path else DEFAULT_VAULT_FILE
        self._master_key_path = (
            Path(master_key_path) if master_key_path else DEFAULT_MASTER_KEY_FILE
        )

    # -- 마스터 키 / Fernet ---------------------------------------------------

    def _fernet(self):
        # Fernet 자체도 cryptography가 필요하므로 지연 임포트한다.
        try:
            from cryptography.fernet import Fernet  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - 패키지 미설치 환경
            raise SecretsError(
                "cryptography 패키지가 설치되어 있지 않습니다. "
                "`pip install cryptography`를 실행하세요."
            ) from exc
        return Fernet(self._load_master_key())

    def _load_master_key(self) -> bytes:
        # 1) 환경변수 — CI/도커 환경에서 파일 없이도 동작하도록 한다.
        env_key = os.environ.get(MASTER_KEY_ENV)
        if env_key:
            return env_key.encode("utf-8")

        # 2) 파일 — 없으면 자동 생성. 자동 생성은 인터랙티브 사용 편의를 위함이며,
        #    백업/이전 전략은 사용자가 직접 챙겨야 한다(README 참조).
        if not self._master_key_path.exists():
            self._generate_master_key()
        self._check_permissions(self._master_key_path)
        return self._master_key_path.read_bytes().strip()

    def _generate_master_key(self) -> None:
        from cryptography.fernet import Fernet  # type: ignore[import-untyped]

        self._master_key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # 권한 600으로 생성 — 다른 사용자가 키 파일을 못 읽도록.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(self._master_key_path, flags, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        logger.info("새 마스터 키를 생성했습니다: %s", self._master_key_path)

    @staticmethod
    def _check_permissions(path: Path) -> None:
        # 운영자가 실수로 파일을 다른 사용자에게 노출하지 않았는지 확인한다.
        # Windows는 stat 모드가 의미가 다르므로 검사 생략.
        if os.name != "posix":
            return
        st = path.stat()
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            logger.warning(
                "마스터 키 파일 권한이 느슨합니다 (%s, mode=%o). 0600으로 조이세요.",
                path,
                mode,
            )

    # -- 볼트 입출력 ---------------------------------------------------------

    def _read_vault(self) -> dict[str, str]:
        if not self._vault_path.exists():
            return {}
        try:
            data = json.loads(self._vault_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretsError(
                f"암호화 볼트를 읽을 수 없습니다 ({self._vault_path}): {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise SecretsError(
                f"암호화 볼트 형식이 올바르지 않습니다 ({self._vault_path})"
            )
        return data

    def _write_vault(self, data: dict[str, str]) -> None:
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        # 0600으로 새 파일을 만들고 atomic rename — 중간 단계 노출 방지.
        tmp = self._vault_path.with_suffix(self._vault_path.suffix + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)
        try:
            os.write(fd, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self._vault_path)

    # -- 백엔드 인터페이스 ---------------------------------------------------

    def get(self, key: str) -> str | None:
        data = self._read_vault()
        token = data.get(key)
        if token is None:
            return None
        try:
            from cryptography.fernet import InvalidToken  # type: ignore[import-untyped]

            return self._fernet().decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretsError(
                f"시크릿 '{key}' 복호화 실패 — 마스터 키가 다르거나 손상되었습니다."
            ) from exc

    def set(self, key: str, value: str) -> None:
        data = self._read_vault()
        token = self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")
        data[key] = token
        self._write_vault(data)

    def delete(self, key: str) -> None:
        data = self._read_vault()
        if key in data:
            del data[key]
            self._write_vault(data)


# ---------------------------------------------------------------------------
# 매니저 (라우터 + 캐시)
# ---------------------------------------------------------------------------


class SecretsManager:
    """참조 문자열을 적절한 백엔드로 라우팅하는 매니저.

    ``resolve()``는 ``"env:KEY"`` 형태의 참조를 파싱해 해당 백엔드에서 값을
    가져온다. 접두어가 없으면 입력값을 그대로 반환한다(레거시 평문 호환).
    """

    def __init__(
        self,
        backends: dict[str, SecretBackend] | None = None,
    ) -> None:
        if backends is None:
            backends = {
                "env": EnvBackend(),
                "keyring": KeyringBackend(),
                "file": EncryptedFileBackend(),
            }
        self._backends = backends

    @property
    def backends(self) -> dict[str, SecretBackend]:
        return dict(self._backends)

    def get_backend(self, scheme: str) -> SecretBackend:
        if scheme not in self._backends:
            raise SecretsError(f"지원하지 않는 백엔드: {scheme}")
        return self._backends[scheme]

    def resolve(self, value: str | None) -> str:
        """참조 문자열 또는 평문을 해소해 실제 시크릿 값을 반환한다.

        - ``None`` 또는 빈 문자열 → 빈 문자열 반환
        - ``"plain:..."`` → 접두어 제거 후 그대로 반환
        - ``"env:NAME"``/``"keyring:NAME"``/``"file:NAME"`` → 해당 백엔드에서 조회
        - 그 외 → 입력값 그대로 반환 (레거시 평문)

        조회 실패 시 빈 문자열을 반환하며 경고 로그를 남긴다 — 시크릿이 없는 것은
        정상적인 운영 상황(예: 텔레그램 미사용)일 수 있으므로 예외를 던지지 않는다.
        """
        if not value:
            return ""

        ref = SecretReference.parse(value)
        if ref is None:
            # 레거시 경로 — 이미 평문이 들어있는 기존 config.yaml 호환.
            return value

        if ref.scheme == "plain":
            return ref.name

        try:
            backend = self.get_backend(ref.scheme)
        except SecretsError as exc:
            logger.warning("%s", exc)
            return ""

        try:
            resolved = backend.get(ref.name)
        except SecretsError as exc:
            logger.warning("시크릿 해소 실패 (%s:%s): %s", ref.scheme, ref.name, exc)
            return ""

        if resolved is None:
            logger.warning(
                "시크릿을 찾을 수 없습니다: %s:%s", ref.scheme, ref.name
            )
            return ""
        return resolved

    def store(self, scheme: str, key: str, value: str) -> None:
        """지정한 백엔드에 시크릿을 저장한다.

        마이그레이션 스크립트와 운영 도구에서 사용한다. ``scheme``이 ``plain``이면
        저장이 무의미하므로 ``SecretsError``를 던진다.
        """
        if scheme == "plain":
            raise SecretsError("plain 스킴은 저장 대상이 아닙니다.")
        self.get_backend(scheme).set(key, value)

    def delete(self, scheme: str, key: str) -> None:
        if scheme == "plain":
            raise SecretsError("plain 스킴은 삭제 대상이 아닙니다.")
        self.get_backend(scheme).delete(key)


# ---------------------------------------------------------------------------
# 모듈 레벨 헬퍼
# ---------------------------------------------------------------------------


_default_manager: SecretsManager | None = None


def default_manager() -> SecretsManager:
    """프로세스 전역에서 공유되는 기본 매니저를 반환한다.

    config.py 등에서 매번 새 인스턴스를 만들지 않도록 캐시한다. 테스트에서는
    ``set_default_manager()``로 갈아끼울 수 있다.
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = SecretsManager()
    return _default_manager


def set_default_manager(manager: SecretsManager | None) -> None:
    """기본 매니저를 교체하거나 ``None``으로 초기화한다 — 테스트 격리용."""
    global _default_manager
    _default_manager = manager


def resolve_secret(value: str | None) -> str:
    """기본 매니저로 참조를 해소한다 — config 로더용 단축 헬퍼."""
    return default_manager().resolve(value)
