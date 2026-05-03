"""Admin API 운영 환경을 한 번에 부트스트랩하는 헬퍼.

`run_bot.py`가 띄우는 ``AdminAPIServer``(BIZ-58)와 Admin UI(`web/admin`)를 같은 머신에
서 처음 켤 때 필요한 세 가지 환경 셋업을 한 번의 호출로 처리한다:

1. **keyring에 ``admin_api_token`` 발급** — 이미 있으면 재사용, 없으면 32바이트
   url-safe 토큰을 생성해 저장한다.
2. **``web/admin/.env.local`` 동기화** — 토큰을 ``ADMIN_API_TOKEN`` 으로 주입하고,
   필요 시 ``ADMIN_API_BASE`` 기본값(``http://127.0.0.1:8082``)도 채워 넣는다.
   기존 파일은 키 단위로 보존하면서 토큰 라인만 갱신해 운영자 로컬 커스텀이 사라지지
   않도록 한다.
3. **``config.yaml``에 ``admin_api`` 블록 보강** — 키가 누락된 경우에만 기본값을 추가
   하고 ``cors_origins``에 Admin UI dev 서버 origin(``http://localhost:3100``)을 합친다.
   이미 사용자가 작성한 값이 있으면 건드리지 않는다.

설계 결정:

- **idempotent**: 이미 모든 게 갖춰져 있으면 변경 없이 안내만 출력한다 — `--force`
  플래그로 토큰을 명시적으로 재발급할 수 있다.
- **secrets 모듈에 위임**: 백엔드 가용성/저장 실패는 ``SecretsManager``가 던지는 예외
  를 그대로 전파해 운영자가 keyring 미설정 등을 즉시 인지하도록 한다.
- **DRY**: ``.env.local`` 파싱은 단순한 ``KEY=VALUE`` 라인만 다루며, Next의 dotenv
  파서와 동일한 의미론을 보장한다(주석/빈 라인 보존).

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

from simpleclaw.security.secrets import SecretsManager

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_ENV_LOCAL_PATH = REPO_ROOT / "web" / "admin" / ".env.local"
DEFAULT_ENV_EXAMPLE_PATH = REPO_ROOT / "web" / "admin" / ".env.local.example"

ADMIN_TOKEN_KEY = "admin_api_token"
DEFAULT_ADMIN_BASE = "http://127.0.0.1:8082"
ADMIN_UI_ORIGIN = "http://localhost:3100"


def ensure_token(*, force: bool = False) -> tuple[str, bool]:
    """keyring에서 토큰을 읽고 없으면 생성해 저장한다.

    Returns:
        (token, created): created=True면 새 토큰을 발급한 것.
    """
    manager = SecretsManager()
    backend = manager.get_backend("keyring")
    existing = backend.get(ADMIN_TOKEN_KEY)
    if existing and not force:
        return existing, False

    new_token = secrets.token_urlsafe(32)
    backend.set(ADMIN_TOKEN_KEY, new_token)
    return new_token, True


def update_env_local(token: str, *, env_path: Path = DEFAULT_ENV_LOCAL_PATH) -> bool:
    """``web/admin/.env.local`` 의 ``ADMIN_API_TOKEN``/``ADMIN_API_BASE`` 라인을 갱신한다.

    파일이 없으면 ``.env.local.example`` 을 시드로 복사한 뒤 토큰을 채운다. 기존 파일은
    라인 단위로 읽어 동일 키만 교체하고, 누락된 키는 끝에 추가한다.

    Returns:
        실제로 디스크 내용이 바뀌었는지 여부.
    """
    desired = {
        "ADMIN_API_TOKEN": token,
        "ADMIN_API_BASE": DEFAULT_ADMIN_BASE,
    }

    if env_path.exists():
        original = env_path.read_text(encoding="utf-8")
        lines = original.splitlines()
    elif DEFAULT_ENV_EXAMPLE_PATH.exists():
        original = DEFAULT_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        lines = original.splitlines()
    else:
        original = ""
        lines = []

    seen_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        # 주석/빈 줄은 그대로 보존 — 운영자 메모를 잃지 않는다.
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in desired and key not in seen_keys:
            new_lines.append(f"{key}={desired[key]}")
            seen_keys.add(key)
        else:
            new_lines.append(line)

    # 미존재 키는 파일 끝에 단순 KEY=VALUE 라인으로 추가한다.
    for key, value in desired.items():
        if key in seen_keys:
            continue
        if new_lines and new_lines[-1] != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")
        seen_keys.add(key)

    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    if new_text == original:
        return False

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(new_text, encoding="utf-8")
    return True


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
    for key, value in defaults.items():
        if key not in admin:
            admin[key] = value
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
        help="keyring에 토큰이 있어도 새로 발급해 덮어쓴다",
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
    args = parser.parse_args(argv)

    token, created = ensure_token(force=args.force)
    if args.print_token:
        print(token)
        return 0

    env_changed = update_env_local(token, env_path=Path(args.env_local))
    config_changed = update_config_yaml(config_path=Path(args.config))

    print("Admin API 셋업 완료:")
    print(f"  - keyring/{ADMIN_TOKEN_KEY}: {'새로 발급' if created else '기존 토큰 재사용'}")
    print(f"  - {args.env_local}: {'갱신' if env_changed else '변경 없음'}")
    print(f"  - {args.config}: {'보강' if config_changed else '변경 없음'}")
    print()
    print("다음 단계:")
    print("  1) 데몬 재기동: .venv/bin/python scripts/run_bot.py")
    print("  2) Admin UI dev 재기동(.env.local 변경 시 필수): cd web/admin && npm run dev")
    print("  3) 헬스 확인: curl -sS http://localhost:3100/api/admin/health")
    return 0


if __name__ == "__main__":
    sys.exit(main())
