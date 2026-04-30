"""실제 자식 프로세스를 사용한 ``kill_process_group`` 통합 테스트.

행 걸린(hung) 자식 프로세스가 SIGTERM을 무시할 때 SIGKILL 폴백이
작동하고, 그룹 잔존 검증/좀비 회수가 끝까지 동작하는지 검증한다.

이 테스트는 실제 ``os.fork``/``setsid``를 사용하므로 Unix 전용이며,
CI에서 PID 누수가 발생하지 않도록 짧은 타임아웃으로 동작한다.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from simpleclaw.security.process import KillResult, kill_process_group, _is_group_alive

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="setsid/killpg는 Unix 전용"
)


async def _spawn(script: str) -> asyncio.subprocess.Process:
    """``setsid``로 격리된 자식 프로세스를 생성한다."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        textwrap.dedent(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=__import__("os").setsid,
    )


async def _wait_for_ready(proc: asyncio.subprocess.Process) -> None:
    """자식 프로세스가 ``READY`` 마커를 stdout에 출력할 때까지 대기.

    파이썬 인터프리터 기동 + 시그널 핸들러 설치가 끝나기 전에 SIGTERM이
    도착하면 핸들러가 등록되지 않아 테스트가 비결정적이 된다.
    """
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
    assert line.strip() == b"READY", f"예상 READY, 실제={line!r}"


class TestHungChildScenarios:
    @pytest.mark.asyncio
    async def test_hung_child_falls_back_to_sigkill(self):
        """SIGTERM을 무시하는 자식은 SIGKILL로 강제 종료되어야 한다."""
        # SIGTERM/SIGINT를 무시하고 무한 sleep — 진짜 행 걸린 자식 시뮬레이션.
        proc = await _spawn(
            """
            import signal, sys, time
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            sys.stdout.write("READY\\n")
            sys.stdout.flush()
            while True:
                time.sleep(60)
            """
        )
        await _wait_for_ready(proc)

        # 매우 짧은 SIGTERM 윈도우 — SIGKILL 경로를 탄다.
        result: KillResult = await kill_process_group(proc, timeout=0.2)

        assert result.terminated is True
        assert result.killed is True, "SIGTERM 무시 자식은 SIGKILL을 받아야 함"
        assert result.group_alive is False, "SIGKILL 후 그룹은 사라져야 함"
        assert proc.returncode is not None

    @pytest.mark.asyncio
    async def test_well_behaved_child_handled_with_sigterm(self):
        """SIGTERM에 정상 응답하는 자식은 SIGKILL 없이 종료되어야 한다."""
        proc = await _spawn(
            """
            import sys, time
            sys.stdout.write("READY\\n")
            sys.stdout.flush()
            try:
                time.sleep(30)
            except KeyboardInterrupt:
                pass
            """
        )
        await _wait_for_ready(proc)

        result = await kill_process_group(proc, timeout=2.0)

        assert result.terminated is True
        assert result.killed is False, "정상 종료 자식에 SIGKILL이 불필요"
        assert result.group_alive is False

    @pytest.mark.asyncio
    async def test_grandchild_in_group_terminated(self):
        """자식이 spawn한 손자 프로세스도 그룹 단위로 종료되어야 한다."""
        proc = await _spawn(
            """
            import os, signal, sys, time
            # 손자 프로세스 fork — 같은 프로세스 그룹에 속함.
            pid = os.fork()
            if pid == 0:
                # 손자: SIGTERM 무시하고 sleep
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                while True:
                    time.sleep(60)
            # 부모(자식): READY 마커 출력 후 sleep
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            sys.stdout.write("READY\\n")
            sys.stdout.flush()
            time.sleep(60)
            """
        )
        await _wait_for_ready(proc)

        pgid = proc.pid
        result = await kill_process_group(proc, timeout=0.2)

        assert result.terminated is True
        # SIGKILL이 그룹 전체에 도달하므로 잔존이 없어야 한다.
        assert result.group_alive is False, (
            f"프로세스 그룹 {pgid}이 SIGKILL 이후에도 살아있음"
        )
        # 운영 환경에선 폴링 직후 확인.
        assert _is_group_alive(pgid) is False

    @pytest.mark.asyncio
    async def test_metrics_sink_records_kill_outcome(self):
        """메트릭 싱크에 SIGTERM/SIGKILL 통계가 누적된다."""
        from simpleclaw.logging.metrics import MetricsCollector

        metrics = MetricsCollector()

        # 1) SIGTERM에 정상 종료되는 자식 — sigterm 카운터 +1.
        proc = await _spawn(
            """
            import sys, time
            sys.stdout.write("READY\\n")
            sys.stdout.flush()
            try:
                time.sleep(30)
            except BaseException:
                pass
            """
        )
        await _wait_for_ready(proc)
        await kill_process_group(proc, timeout=2.0, metrics=metrics)

        # 2) SIGTERM을 무시하는 자식 — sigkill 카운터 +1.
        proc2 = await _spawn(
            """
            import signal, sys, time
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            sys.stdout.write("READY\\n")
            sys.stdout.flush()
            while True:
                time.sleep(60)
            """
        )
        await _wait_for_ready(proc2)
        await kill_process_group(proc2, timeout=0.2, metrics=metrics)

        snap = metrics.get_snapshot()
        assert snap.process_kills_sigterm >= 1
        assert snap.process_kills_sigkill >= 1
        # 정상 종료된 두 시나리오 모두 그룹 누수가 없어야 한다.
        assert snap.process_group_leaks == 0
