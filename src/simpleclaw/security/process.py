"""프로세스 그룹 격리 유틸리티 모듈.

서브프로세스를 독립된 프로세스 그룹으로 실행하고, 타임아웃 시
그룹 전체를 종료하여 좀비 프로세스를 방지한다.

설계 결정:
- os.setsid()로 새 세션을 생성하여 자식 프로세스를 격리
- SIGTERM → 대기 → SIGKILL 순서로 단계적 종료 (graceful shutdown 우선)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Callable

logger = logging.getLogger(__name__)


def get_preexec_fn() -> Callable[[], None] | None:
    """Unix에서는 ``os.setsid``를, Windows에서는 ``None``을 반환한다."""
    if sys.platform == "win32":
        return None
    return os.setsid


async def kill_process_group(
    process: asyncio.subprocess.Process,
    timeout: float = 5.0,
) -> None:
    """*process*의 프로세스 그룹 전체를 종료한다.

    SIGTERM을 먼저 전송하고, *timeout*초 내에 종료되지 않으면 SIGKILL을 보낸다.
    이미 종료된 프로세스는 조용히 무시한다.
    """
    if process.returncode is not None:
        return  # 이미 종료된 프로세스

    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return  # 프로세스가 이미 사라진 경우

    # 그룹에 SIGTERM 전송 — 정상 종료 기회 부여
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    # 정상 종료 대기
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        pass

    # 정상 종료 실패 시 SIGKILL로 강제 종료
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass

    try:
        await process.wait()
    except Exception:
        pass

    logger.warning("Force-killed process group pgid=%d (pid=%d)", pgid, pid)
