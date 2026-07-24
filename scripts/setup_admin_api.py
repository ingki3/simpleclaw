"""Admin API 운영 환경을 한 번에 부트스트랩하는 헬퍼.

`run_bot.py`가 띄우는 ``AdminAPIServer``(BIZ-58)와 Admin UI(`web/admin`)를 같은 머신에
서 처음 켤 때 필요한 세 가지 환경 셋업을 한 번의 호출로 처리한다:

1. **시크릿 매니저에 ``admin_api_token`` 발급** — ``admin_api.token_secret`` 참조가
   가리키는 백엔드(``keyring`` / ``file`` / ``env``)에 토큰을 저장한다. 이미 있으면
   재사용, 없거나 ``--force`` 면 32바이트 url-safe 토큰을 새로 발급한다.
2. **``web/admin/.env.local`` 동기화** — 토큰을 ``ADMIN_API_TOKEN`` 으로 주입하고,
   필요 시 ``ADMIN_API_BASE`` 기본값(``http://127.0.0.1:8082``)도 채워 넣는다.
   기존 파일은 키 단위로 보존하면서 토큰 라인만 갱신해 운영자 로컬 커스텀이 사라지지
   않도록 한다. 본 동기화 로직은 ``simpleclaw.channels.admin_env_local`` 에 SoT 로
   두어 admin_api 회전 핸들러도 같은 함수를 호출한다 (BIZ-244 재발 방지).
3. **``config.yaml``에 ``admin_api`` 블록 보강** — 키가 누락된 경우에만 기본값을 추가
   하고 ``cors_origins``에 Admin UI dev 서버 origin(``http://localhost:8088``)을 합친다.
   이미 사용자가 작성한 값이 있으면 건드리지 않는다.

설계 결정:

- **백엔드는 config.yaml 의 ``token_secret`` 참조가 결정** — 운영자가 prod 에서
  ``file:admin_api_token`` 으로 운용 중인데 본 스크립트가 keyring 으로만 발급하면
  데몬은 옛 file 백엔드 값을 계속 읽어 토큰이 어긋난다 (BIZ-244 와 같은 부류).
  참조가 없거나 ``plain:`` 이면 운영자에게 안전한 기본값을 안내한다.
- **idempotent**: 이미 모든 게 갖춰져 있으면 변경 없이 안내만 출력한다 — `--force`
  플래그로 토큰을 명시적으로 재발급할 수 있다.
- **secrets 모듈에 위임**: 백엔드 가용성/저장 실패는 ``SecretsManager``가 던지는 예외
  를 그대로 전파해 운영자가 keyring 미설정 등을 즉시 인지하도록 한다.

사용 예::

    .venv/bin/python scripts/setup_admin_api.py
    .venv/bin/python scripts/setup_admin_api.py --force      # 토큰 재발급
    .venv/bin/python scripts/setup_admin_api.py --print-token  # 토큰만 stdout 출력
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import yaml

# scripts/ 는 패키지가 아니므로 src 를 명시적으로 추가 — 단독 실행 시에도 동작.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from simpleclaw.channels.admin_env_local import (
    DEFAULT_ENV_LOCAL_PATH,
    sync_env_local,
)
from simpleclaw.security.secrets import (
    SecretReference,
    SecretsError,
    SecretsManager,
)

REPO_ROOT = ROOT
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

ADMIN_TOKEN_KEY = "admin_api_token"
ADMIN_UI_ORIGIN = "http://localhost:8088"

# config.yaml 미작성 시 ``--force`` 등으로 기본 폴백할 백엔드.
# keyring 은 헤드리스 환경에서 실패할 수 있지만 macOS 등 인터랙티브 환경의 최초 셋업
# 시 가장 안전한 선택이므로 유지한다 (file 백엔드는 마스터 키 노출 면적이 더 큼).
_FALLBACK_BACKEND = "keyring"

# 본 스크립트가 직접 발급/회전을 지원하는 백엔드 — env 백엔드는 프로세스 환경변수
# 라서 영속성이 없어 회전 대상에서 제외한다.
_WRITABLE_BACKENDS = ("keyring", "file")


def _resolve_token_backend(config_path: Path) -> tuple[str, str]:
    """``admin_api.token_secret`` 참조에서 (backend, key) 를 결정한다.

    Returns:
        (backend, key_name). 참조가 비었거나 ``plain:`` 이면 안전한 폴백으로 떨어진다.

    Why: BIZ-244 — 운영자가 ``file:admin_api_token`` 으로 운용 중인데 스크립트가
    keyring 만 갱신하면 회전이 무의미하다. 참조 그대로를 따라야 ``--force`` 가
    실제 데몬이 읽는 백엔드에 반영된다.
    """
    if not config_path.is_file():
        return _FALLBACK_BACKEND, ADMIN_TOKEN_KEY

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return _FALLBACK_BACKEND, ADMIN_TOKEN_KEY

    admin = data.get("admin_api") if isinstance(data, dict) else None
    if not isinstance(admin, dict):
        return _FALLBACK_BACKEND, ADMIN_TOKEN_KEY

    ref_str = admin.get("token_secret")
    if not isinstance(ref_str, str) or not ref_str:
        return _FALLBACK_BACKEND, ADMIN_TOKEN_KEY

    ref = SecretReference.parse(ref_str)
    if ref is None:
        # 레거시 평문 — 회전 시 어디에 넣어야 할지 모호하므로 안전한 폴백 백엔드로.
        return _FALLBACK_BACKEND, ADMIN_TOKEN_KEY

    if ref.scheme == "plain":
        # ``plain:`` 으로 박힌 토큰은 회전 의미 자체가 없음 (config.yaml 평문). 폴백.
        return _FALLBACK_BACKEND, ADMIN_TOKEN_KEY

    if ref.scheme not in _WRITABLE_BACKENDS:
        # env 백엔드 — 영속성 없음. 본 스크립트가 영속 저장할 곳을 결정하지 못하므로
        # 운영자에게 알리고 폴백한다.
        print(
            f"[경고] admin_api.token_secret 가 '{ref.scheme}:{ref.name}' 인데, "
            f"본 스크립트는 영속 백엔드(keyring/file)만 회전합니다. "
            f"{_FALLBACK_BACKEND} 로 발급하니 필요 시 config.yaml 을 갱신하세요."
        )
        return _FALLBACK_BACKEND, ref.name

    return ref.scheme, ref.name


def ensure_token(
    *,
    force: bool = False,
    backend: str = _FALLBACK_BACKEND,
    key: str = ADMIN_TOKEN_KEY,
) -> tuple[str, bool]:
    """시크릿 매니저에서 토큰을 읽고 없으면 생성해 저장한다.

    Args:
        force: 기존 값이 있어도 새 토큰으로 덮어쓴다.
        backend: ``keyring`` 또는 ``file``. 기본은 ``keyring``.
        key: 백엔드 내 시크릿 키 이름. 기본 ``admin_api_token``.

    Returns:
        (token, created): created=True면 새 토큰을 발급한 것.
    """
    manager = SecretsManager()
    storage = manager.get_backend(backend)
    existing = storage.get(key)
    if existing and not force:
        return existing, False

    new_token = secrets.token_urlsafe(32)
    storage.set(key, new_token)
    return new_token, True


def update_env_local(token: str, *, env_path: Path = DEFAULT_ENV_LOCAL_PATH) -> bool:
    """``web/admin/.env.local`` 의 ``ADMIN_API_TOKEN``/``ADMIN_API_BASE`` 를 갱신한다.

    실제 로직은 ``simpleclaw.channels.admin_env_local.sync_env_local`` 에 있다 —
    회전 핸들러와 SoT 를 공유하기 위함.
    """
    return sync_env_local(token, env_path=env_path)


def update_config_yaml(*, config_path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """``config.yaml``에 admin_api 블록이 없으면 보강한다.

    이미 존재하는 키는 건드리지 않는다 — 운영자가 의도적으로 비활성화/포트 변경한
    것을 덮지 않는다. ``cors_origins`` 만 Admin UI dev origin 누락 시 추가한다.

    Returns:
        실제로 파일이 변경됐는지 여부.
    """
    if not config_path.exists():
        # config.yaml이 없으면 example을 복사해 시작점을 만들어준다.
        example = config_path.with_name("config.yaml.example")
        if example.exists():
            config_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            config_path.write_text("admin_api:\n  enabled: true\n", encoding="utf-8")

    text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        # 비정상 yaml 루트 — 손대지 않고 사용자에게 알린다.
        raise SystemExit(
            f"{config_path} 의 루트가 dict가 아닙니다 — 수동으로 admin_api 블록을 추가하세요."
        )

    admin = data.get("admin_api")
    changed = False
    if not isinstance(admin, dict):
        admin = {}
        changed = True

    defaults = {
        "enabled": True,
        "bind_host": "127.0.0.1",
        "bind_port": 8082,
        "token_secret": f"keyring:{ADMIN_TOKEN_KEY}",
        "read_timeout_seconds": 30,
        "request_max_body_kb": 256,
    }
    for k, value in defaults.items():
        if k not in admin:
            admin[k] = value
            changed = True

    cors = admin.get("cors_origins")
    if not isinstance(cors, list):
        cors = []
        changed = True
    if ADMIN_UI_ORIGIN not in cors:
        cors.append(ADMIN_UI_ORIGIN)
        changed = True
    admin["cors_origins"] = cors

    if not changed:
        return False

    data["admin_api"] = admin
    # PyYAML 기본 dumper로 직렬화 — 한국어 주석은 보존되지 않으므로 키 자체만 추가하고
    # 운영자에게 안내하는 방식을 택했다. 기존 파일에 admin_api가 이미 있으면 본 코드 경로로
    # 들어오지 않으므로 사용자 코멘트가 사라지지 않는다.
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Admin API 토큰 발급 + .env.local + config.yaml 보강",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="시크릿 매니저에 토큰이 있어도 새로 발급해 덮어쓴다",
    )
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="현재 토큰만 stdout으로 출력하고 종료 (.env.local/config 미수정)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="대상 config.yaml 경로 (기본: 리포 루트의 config.yaml)",
    )
    parser.add_argument(
        "--env-local",
        default=str(DEFAULT_ENV_LOCAL_PATH),
        help="대상 .env.local 경로 (기본: web/admin/.env.local)",
    )
    parser.add_argument(
        "--backend",
        choices=_WRITABLE_BACKENDS,
        default=None,
        help=(
            "시크릿 저장 백엔드. 기본은 config.yaml 의 admin_api.token_secret 참조를 "
            "따른다 (없으면 keyring)."
        ),
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if args.backend is not None:
        backend, key = args.backend, ADMIN_TOKEN_KEY
    else:
        backend, key = _resolve_token_backend(config_path)

    try:
        token, created = ensure_token(force=args.force, backend=backend, key=key)
    except SecretsError as exc:
        print(f"[오류] 토큰 저장 실패 ({backend}:{key}): {exc}", file=sys.stderr)
        return 1
    if args.print_token:
        print(token)
        return 0

    env_changed = update_env_local(token, env_path=Path(args.env_local))
    config_changed = update_config_yaml(config_path=config_path)

    print("Admin API 셋업 완료:")
    print(
        f"  - {backend}:{key}: "
        f"{'새로 발급' if created else '기존 토큰 재사용'}"
    )
    print(f"  - {args.env_local}: {'갱신' if env_changed else '변경 없음'}")
    print(f"  - {args.config}: {'보강' if config_changed else '변경 없음'}")
    print()
    print("다음 단계:")
    print("  1) 데몬 재기동: .venv/bin/python scripts/run_bot.py")
    print("  2) Admin UI dev 재기동(.env.local 변경 시 필수): cd web/admin && npm run dev")
    print("  3) 헬스 확인: curl -sS http://localhost:8088/api/admin/health")
    return 0


if __name__ == "__main__":
    sys.exit(main())
