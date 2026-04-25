"""보안 서브시스템 — 명령어 실행 경화(hardening) 모듈 패키지.

서브프로세스 실행 시 보안을 강화하기 위한 세 가지 핵심 기능을 제공한다:
1. env_filter: 환경변수에서 민감 정보(API 키, 토큰 등)를 제거
2. guard: 위험한 셸 명령어를 패턴 기반으로 탐지·차단
3. process: 프로세스 그룹 격리 및 좀비 프로세스 방지
"""

from simpleclaw.security.env_filter import filter_env
from simpleclaw.security.guard import CommandGuard, DangerousCommandError
from simpleclaw.security.process import get_preexec_fn, kill_process_group

__all__ = [
    "CommandGuard",
    "DangerousCommandError",
    "filter_env",
    "get_preexec_fn",
    "kill_process_group",
]
