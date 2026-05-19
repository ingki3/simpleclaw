"""``web/admin/.env.local`` 와 ``admin_api_token`` 동기화 헬퍼 (BIZ-245).

토큰을 회전할 때마다 Next 프록시가 읽는 ``web/admin/.env.local`` 의
``ADMIN_API_TOKEN`` 라인을 함께 갱신해 데몬의 ``auth_token`` 과 항상 일치하도록 한다.

BIZ-244 사후 재발 방지 — vault(``~/.simpleclaw/secrets.enc``)만 회전되고
``.env.local`` 이 stale 인 상태로 방치되면 Next 프록시가 옛 Bearer 토큰을 계속
주입해 모든 admin UI 패널이 401 빈 상태로 떨어진다.

설계 결정:

- **단일 SoT 함수**: ``scripts/setup_admin_api.py`` 와 admin_api 회전 핸들러
  (``admin_api.py:_handle_rotate_secret``) 모두 본 모듈의 ``sync_env_local`` 만
  호출하도록 통합 — 회전 경로가 늘어나도 동기화 로직이 한 곳에서 갱신된다.
- **idempotent**: 토큰 값이 이미 일치하면 디스크 쓰기를 생략하고 ``False`` 반환.
  반복 호출이 안전해 데몬 측에서도 매 회전마다 부담 없이 부를 수 있다.
- **기존 운영자 커스텀 보존**: ``ADMIN_API_TOKEN``/``ADMIN_API_BASE`` 두 키만
  타겟팅해 라인 단위로 교체하고, 주석·빈 줄·기타 키는 그대로 유지한다.
- **파일 부재 허용**: ``.env.local`` 이 없으면 ``.env.local.example`` 을 시드로 복사,
  example 도 없으면 빈 파일에서 키만 채워 생성. 신선 셋업 + 운영 자동 동기화 모두
  같은 함수로 처리 가능.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 리포 루트 — src/simpleclaw/channels/ 기준 세 단계 위.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_LOCAL_PATH = REPO_ROOT / "web" / "admin" / ".env.local"
DEFAULT_ENV_EXAMPLE_PATH = REPO_ROOT / "web" / "admin" / ".env.local.example"
DEFAULT_ADMIN_BASE = "http://127.0.0.1:8082"

# 동기화 대상 키 — 운영자가 수동으로 추가한 다른 키(PORT 등)는 건드리지 않는다.
TOKEN_KEY = "ADMIN_API_TOKEN"
BASE_KEY = "ADMIN_API_BASE"


def sync_env_local(
    token: str,
    *,
    env_path: Path | None = None,
    base_url: str = DEFAULT_ADMIN_BASE,
    example_path: Path | None = None,
) -> bool:
    """``.env.local`` 의 ``ADMIN_API_TOKEN`` / ``ADMIN_API_BASE`` 라인을 갱신한다.

    Args:
        token: 새로 발급/회전된 admin API 토큰 (평문).
        env_path: ``.env.local`` 경로. 미지정 시 리포 기본 위치 사용.
        base_url: ``ADMIN_API_BASE`` 의 기본값. 운영자가 직접 다른 값을 넣어두지
            않은 경우에만 채워 넣는다.
        example_path: 시드 템플릿. ``.env.local`` 미존재 시 본 파일을 복사한 뒤 키만 갱신.

    Returns:
        실제로 디스크 내용이 바뀌었는지 여부.
    """
    env_path = Path(env_path) if env_path is not None else DEFAULT_ENV_LOCAL_PATH
    example_path = (
        Path(example_path) if example_path is not None else DEFAULT_ENV_EXAMPLE_PATH
    )

    desired = {
        TOKEN_KEY: token,
        BASE_KEY: base_url,
    }

    if env_path.exists():
        original = env_path.read_text(encoding="utf-8")
        lines = original.splitlines()
    elif example_path.exists():
        original = example_path.read_text(encoding="utf-8")
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


def make_secret_rotation_callback(
    *,
    env_path: Path | None = None,
    base_url: str = DEFAULT_ADMIN_BASE,
):
    """admin_api 회전 핸들러에 꽂을 콜백 팩토리.

    핸들러는 ``(backend, name, new_value)`` 시그니처로 호출하며, 본 콜백은
    ``name == "admin_api_token"`` 인 경우에만 ``.env.local`` 을 동기화한다.

    Why factory: ``env_path`` 같은 운영 환경 파라미터를 클로저로 묶어두어
    핸들러 인터페이스는 데이터 3개로 좁게 유지한다. 미존재/권한 오류는 경고 로그만
    남기고 회전 자체는 성공으로 처리 — 토큰 회전 액션을 ``.env.local`` 쓰기
    실패로 막아 운영자가 401 보다 더 큰 장애로 떨어지지 않도록.
    """

    def _callback(backend: str, name: str, new_value: str) -> None:
        if name != "admin_api_token":
            return
        try:
            changed = sync_env_local(
                new_value, env_path=env_path, base_url=base_url
            )
        except OSError as exc:
            logger.warning(
                "admin_api_token 회전 후 .env.local 동기화 실패 (%s): %s — "
                "수동으로 web/admin/.env.local 의 ADMIN_API_TOKEN 을 갱신하세요.",
                env_path or DEFAULT_ENV_LOCAL_PATH,
                exc,
            )
            return
        if changed:
            logger.info(
                "admin_api_token 회전 — %s 의 ADMIN_API_TOKEN 라인을 새 토큰으로 갱신했습니다.",
                env_path or DEFAULT_ENV_LOCAL_PATH,
            )

    return _callback


__all__ = [
    "BASE_KEY",
    "DEFAULT_ADMIN_BASE",
    "DEFAULT_ENV_EXAMPLE_PATH",
    "DEFAULT_ENV_LOCAL_PATH",
    "REPO_ROOT",
    "TOKEN_KEY",
    "make_secret_rotation_callback",
    "sync_env_local",
]
