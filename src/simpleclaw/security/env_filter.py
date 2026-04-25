"""서브프로세스 환경변수 필터링 모듈.

서브프로세스 실행 전 os.environ에서 민감한 키(API 키, 토큰, 시크릿 등)를
제거한 사본을 반환한다. 이를 통해 자식 프로세스에 비밀 정보가 노출되는 것을 방지한다.

동작 흐름:
1. DEFAULT_BLOCKLIST의 glob 패턴과 환경변수 키를 매칭
2. 매칭된 키를 제거한 환경변수 사본을 반환
3. passthrough 목록에 포함된 키는 블록리스트와 매칭되어도 유지
"""

from __future__ import annotations

import fnmatch
import os

# 서브프로세스 환경에서 제거할 키의 glob 패턴 목록
DEFAULT_BLOCKLIST: list[str] = [
    "*_API_KEY",
    "*_TOKEN",
    "*_SECRET",
    "*_PASSWORD",
    "TELEGRAM_*",
    "OPENAI_*",
    "ANTHROPIC_*",
    "GOOGLE_*",
    "AWS_*",
    "WEBHOOK_*",
    "GH_TOKEN",
    "GITHUB_*",
]


def filter_env(
    passthrough: list[str] | None = None,
    blocklist: list[str] | None = None,
) -> dict[str, str]:
    """민감한 키를 제거한 ``os.environ`` 사본을 반환한다.

    Args:
        passthrough: 블록리스트에 매칭되더라도 유지할 키 목록.
        blocklist: 기본 블록리스트 패턴을 대체할 커스텀 패턴 목록.

    Returns:
        서브프로세스에 안전하게 전달할 수 있는 필터링된 환경변수 딕셔너리.
    """
    patterns = blocklist if blocklist is not None else DEFAULT_BLOCKLIST
    keep = set(passthrough or [])
    env = dict(os.environ)

    to_remove = []
    for key in env:
        if key in keep:
            continue
        for pattern in patterns:
            if fnmatch.fnmatch(key, pattern):
                to_remove.append(key)
                break

    for key in to_remove:
        del env[key]

    return env
