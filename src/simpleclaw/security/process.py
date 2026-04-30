"""프로세스 그룹 격리 유틸리티 모듈.

서브프로세스를 독립된 프로세스 그룹으로 실행하고, 타임아웃 시
그룹 전체를 종료하여 좀비 프로세스를 방지한다.

설계 결정:
- ``os.setsid()``로 새 세션을 생성하여 자식 프로세스를 격리.
- SIGTERM → 폴링 → SIGKILL 순서의 단계적 종료(graceful shutdown 우선).
- SIGKILL 후에도 그룹 잔존 여부를 폴링으로 확인하고, ``waitpid(WNOHANG)``
  루프로 좀비를 회수해 24/7 데몬 운영 시 PID/메모리 누수를 차단.
- 종료 결과는 :class:`KillResult`로 반환되며, 호출 측은 메트릭/로그에
  활용할 수 있다.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import signal
import sys
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


# SIGKILL 후 그룹 잔존 여부를 확인하는 폴링 간격/총 시도 한도.
# - 운영 환경에서 SIGKILL은 보통 수십 ms 내에 반영되므로 짧은 간격으로 충분하다.
# - 잔존이 의심되는 경우에 한해 외부에 시그널을 노출(메트릭/로그)한다.
_POLL_INTERVAL = 0.05
_POLL_MAX_ATTEMPTS = 20  # 0.05 * 20 = 약 1초


class _KillMetricsSink(Protocol):
    """``record_process_kill`` 시그니처를 만족하는 메트릭 싱크 프로토콜.

    ``simpleclaw.logging.metrics.MetricsCollector`` 의존을 피하기 위해
    구조적 타이핑(duck typing)으로 받는다.
    """

    def record_process_kill(
        self,
        *,
        killed: bool,
        group_alive: bool,
        reaped_zombies: int,
    ) -> None: ...


@dataclass(frozen=True)
class KillResult:
    """``kill_process_group`` 호출 결과.

    Attributes:
        terminated: SIGTERM(또는 그 이전 종료)으로 정상 종료되었는지 여부.
        killed: SIGKILL이 전송되었는지 여부 (강제 종료 발생 시 True).
        group_alive: 종료 시도 후에도 프로세스 그룹이 잔존하는지 여부.
            True인 경우 좀비/잔존 프로세스 누수 가능성을 의미한다.
        reaped_zombies: ``waitpid(WNOHANG)`` 루프에서 회수한 좀비 자식 수.
        pgid: 대상 프로세스 그룹 ID (``getpgid`` 호출 실패 시 ``None``).
    """

    terminated: bool = False
    killed: bool = False
    group_alive: bool = False
    reaped_zombies: int = 0
    pgid: int | None = None


def get_preexec_fn() -> Callable[[], None] | None:
    """Unix에서는 ``os.setsid``를, Windows에서는 ``None``을 반환한다."""
    if sys.platform == "win32":
        return None
    return os.setsid


def _is_group_alive(pgid: int) -> bool:
    """``killpg(pgid, 0)``으로 프로세스 그룹 잔존 여부를 확인한다.

    시그널 0은 실제 시그널을 보내지 않고 권한/존재 검사만 수행한다.
    그룹이 사라졌으면 ``ProcessLookupError``(ESRCH), 살아 있으면 정상 반환.
    """
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 권한 거부는 "프로세스가 존재"한다는 신호이므로 잔존으로 간주한다.
        return True
    except OSError as exc:
        # ESRCH는 그룹이 사라진 정상 케이스, 그 외 오류는 보수적으로 잔존으로 본다.
        if exc.errno == errno.ESRCH:
            return False
        return True


def _reap_zombies() -> int:
    """현재 프로세스의 좀비 자식들을 ``waitpid(WNOHANG)`` 루프로 회수한다.

    ``asyncio.subprocess``는 자체적으로 자식 종료를 감지하지만, 손자 프로세스가
    부모(에이전트 프로세스)에 재부모화된 경우 좀비로 남을 수 있다. WNOHANG으로
    블로킹 없이 회수 가능한 좀비를 모두 거둬들여 PID 누수를 방지한다.

    Returns:
        실제로 회수한 좀비 자식 프로세스의 수.
    """
    if sys.platform == "win32":
        return 0

    reaped = 0
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            # 회수할 자식이 더 이상 없음.
            break
        except OSError as exc:
            logger.debug("waitpid 회수 중 OSError: %s", exc)
            break
        if pid == 0:
            # 종료된 자식이 더 이상 없으면 0 반환 (Linux/macOS 공통 동작).
            break
        reaped += 1
    return reaped


async def _poll_group_dead(pgid: int) -> bool:
    """``pgid`` 그룹이 사라질 때까지 짧은 간격으로 폴링한다.

    Returns:
        지정된 폴링 윈도우 내에 그룹이 소멸했으면 True, 잔존하면 False.
    """
    for _ in range(_POLL_MAX_ATTEMPTS):
        if not _is_group_alive(pgid):
            return True
        await asyncio.sleep(_POLL_INTERVAL)
    return not _is_group_alive(pgid)


async def kill_process_group(
    process: asyncio.subprocess.Process,
    timeout: float = 5.0,
    *,
    metrics: _KillMetricsSink | None = None,
) -> KillResult:
    """*process*의 프로세스 그룹 전체를 종료한다.

    SIGTERM을 먼저 전송하고 *timeout*초 내에 종료되지 않으면 SIGKILL을 보낸다.
    SIGKILL 이후에도 그룹이 잔존하는지 폴링하고, 손자 프로세스가 좀비로
    남은 경우 ``waitpid(WNOHANG)`` 루프로 회수한다.

    Args:
        process: 종료할 ``asyncio`` 서브프로세스.
        timeout: SIGTERM 후 자발적 종료를 기다리는 시간(초).

    Returns:
        종료 처리 결과를 담은 :class:`KillResult`. 호출 측은 ``group_alive``
        값을 확인해 자원 누수 메트릭을 갱신할 수 있다.
    """
    result = await _kill_process_group_impl(process, timeout)
    if metrics is not None:
        # 메트릭 기록은 종료 경로 전반에서 단일 지점으로 집계한다.
        try:
            metrics.record_process_kill(
                killed=result.killed,
                group_alive=result.group_alive,
                reaped_zombies=result.reaped_zombies,
            )
        except Exception as exc:  # noqa: BLE001 — 메트릭 실패가 종료를 막지 않게.
            logger.debug("kill 메트릭 기록 실패: %s", exc)
    return result


async def _kill_process_group_impl(
    process: asyncio.subprocess.Process,
    timeout: float,
) -> KillResult:
    """``kill_process_group``의 실제 구현부 — 메트릭 기록을 분리하기 위해 내부화."""
    # 이미 종료된 프로세스라도 좀비가 남아 있을 수 있으므로 회수만 수행한다.
    if process.returncode is not None:
        reaped = _reap_zombies()
        return KillResult(terminated=True, reaped_zombies=reaped)

    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        # 프로세스가 이미 사라졌어도 좀비 회수는 시도한다.
        reaped = _reap_zombies()
        return KillResult(terminated=True, reaped_zombies=reaped)

    # 1) SIGTERM 전송 — 정상 종료 기회 부여.
    sigterm_sent = True
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        # 그룹이 이미 사라짐 — 좀비만 회수한다.
        reaped = _reap_zombies()
        return KillResult(
            terminated=True,
            reaped_zombies=reaped,
            pgid=pgid,
        )
    except PermissionError as exc:
        logger.error(
            "SIGTERM 권한 거부 pgid=%d: %s — 강제 종료를 시도합니다", pgid, exc
        )
        sigterm_sent = False

    # 2) 정상 종료 대기. SIGTERM이 거부됐다면 대기를 건너뛰고 곧장 SIGKILL.
    if sigterm_sent:
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            # 메인 프로세스가 종료됐어도 그룹/좀비 잔존 가능성을 검증한다.
            group_alive = _is_group_alive(pgid)
            reaped = _reap_zombies()
            if group_alive:
                logger.warning(
                    "프로세스 그룹 잔존 감지 (SIGTERM 후) pgid=%d", pgid
                )
            return KillResult(
                terminated=True,
                killed=False,
                group_alive=group_alive,
                reaped_zombies=reaped,
                pgid=pgid,
            )
        except asyncio.TimeoutError:
            pass

    # 3) SIGKILL로 강제 종료.
    kill_sent = True
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        kill_sent = False
    except PermissionError as exc:
        logger.error("SIGKILL 권한 거부 pgid=%d: %s", pgid, exc)
        kill_sent = False

    # 메인 프로세스의 wait는 짧은 시간 내 반환되어야 하지만, 안전하게 별도
    # 짧은 타임아웃으로 감싸 영구 블로킹을 막는다.
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.error("SIGKILL 후에도 process.wait() 미반환 pid=%d", pid)
    except Exception as exc:  # noqa: BLE001 — 종료 경로는 모든 예외를 흡수.
        logger.debug("process.wait() 예외 무시: %s", exc)

    # 4) 그룹 잔존 여부 폴링 + 좀비 회수.
    group_dead = await _poll_group_dead(pgid)
    reaped = _reap_zombies()

    if not group_dead:
        logger.error(
            "SIGKILL 이후에도 프로세스 그룹 잔존 pgid=%d (PID 누수 가능)", pgid
        )
    else:
        logger.warning(
            "프로세스 그룹 강제 종료 완료 pgid=%d (pid=%d, reaped=%d)",
            pgid,
            pid,
            reaped,
        )

    return KillResult(
        terminated=True,
        killed=kill_sent,
        group_alive=not group_dead,
        reaped_zombies=reaped,
        pgid=pgid,
    )
