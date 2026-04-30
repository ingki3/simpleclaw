"""Tests for process group isolation utilities."""

import asyncio
import os
import signal
import sys
from unittest.mock import MagicMock, patch

import pytest

from simpleclaw.security.process import (
    KillResult,
    _is_group_alive,
    _reap_zombies,
    get_preexec_fn,
    kill_process_group,
)


class TestGetPreexecFn:
    def test_returns_setsid_on_unix(self):
        if sys.platform == "win32":
            pytest.skip("Unix-only test")
        fn = get_preexec_fn()
        assert fn is os.setsid

    @patch("simpleclaw.security.process.sys")
    def test_returns_none_on_windows(self, mock_sys):
        mock_sys.platform = "win32"
        fn = get_preexec_fn()
        assert fn is None


class TestKillProcessGroup:
    @pytest.mark.asyncio
    async def test_skip_if_already_finished(self):
        proc = MagicMock()
        proc.returncode = 0  # Already finished
        with patch("simpleclaw.security.process._reap_zombies", return_value=0):
            result = await kill_process_group(proc)
        assert isinstance(result, KillResult)
        assert result.terminated is True
        assert result.killed is False
        assert result.group_alive is False

    @pytest.mark.asyncio
    async def test_handles_process_lookup_error(self):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 99999

        with patch("os.getpgid", side_effect=ProcessLookupError), \
             patch("simpleclaw.security.process._reap_zombies", return_value=0):
            result = await kill_process_group(proc)
        assert result.terminated is True
        assert result.group_alive is False

    @pytest.mark.asyncio
    async def test_sigterm_then_wait(self):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 12345

        # Simulate process exits after SIGTERM
        wait_future = asyncio.get_event_loop().create_future()
        wait_future.set_result(0)
        proc.wait = MagicMock(return_value=wait_future)

        with patch("os.getpgid", return_value=12345), \
             patch("os.killpg") as mock_killpg, \
             patch(
                 "simpleclaw.security.process._is_group_alive",
                 return_value=False,
             ), \
             patch("simpleclaw.security.process._reap_zombies", return_value=0):
            result = await kill_process_group(proc, timeout=1.0)

        # Should have sent SIGTERM only.
        mock_killpg.assert_called_once_with(12345, signal.SIGTERM)
        assert result.killed is False
        assert result.group_alive is False

    @pytest.mark.asyncio
    async def test_sigkill_after_timeout(self):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 12345

        # Simulate process does NOT exit after SIGTERM (wait times out)
        async def slow_wait():
            await asyncio.sleep(10)

        proc.wait = slow_wait

        with patch("os.getpgid", return_value=12345), \
             patch("os.killpg") as mock_killpg, \
             patch(
                 "simpleclaw.security.process._poll_group_dead",
                 return_value=True,
             ), \
             patch("simpleclaw.security.process._reap_zombies", return_value=0):
            result = await kill_process_group(proc, timeout=0.1)

        # Should have sent both SIGTERM and SIGKILL
        calls = mock_killpg.call_args_list
        assert len(calls) == 2
        assert calls[0].args == (12345, signal.SIGTERM)
        assert calls[1].args == (12345, signal.SIGKILL)
        assert result.killed is True
        assert result.group_alive is False

    @pytest.mark.asyncio
    async def test_group_alive_after_sigkill_reports_leak(self):
        """SIGKILL 후에도 그룹이 잔존하면 ``group_alive=True``로 보고한다."""
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 12345

        async def slow_wait():
            await asyncio.sleep(10)

        proc.wait = slow_wait

        with patch("os.getpgid", return_value=12345), \
             patch("os.killpg"), \
             patch(
                 "simpleclaw.security.process._poll_group_dead",
                 return_value=False,  # 그룹이 사라지지 않음
             ), \
             patch("simpleclaw.security.process._reap_zombies", return_value=2):
            result = await kill_process_group(proc, timeout=0.05)

        assert result.killed is True
        assert result.group_alive is True
        assert result.reaped_zombies == 2

    @pytest.mark.asyncio
    async def test_metrics_sink_invoked(self):
        """``metrics`` 인자가 주어지면 ``record_process_kill``로 결과를 보고한다."""
        proc = MagicMock()
        proc.returncode = 0  # 이미 종료된 프로세스 경로

        sink = MagicMock()

        with patch("simpleclaw.security.process._reap_zombies", return_value=3):
            result = await kill_process_group(proc, metrics=sink)

        sink.record_process_kill.assert_called_once_with(
            killed=False,
            group_alive=False,
            reaped_zombies=3,
        )
        assert result.reaped_zombies == 3

    @pytest.mark.asyncio
    async def test_metrics_sink_failure_does_not_propagate(self):
        """메트릭 기록 실패가 종료 경로를 막지 않아야 한다."""
        proc = MagicMock()
        proc.returncode = 0

        sink = MagicMock()
        sink.record_process_kill.side_effect = RuntimeError("metric pipeline down")

        with patch("simpleclaw.security.process._reap_zombies", return_value=0):
            # 예외 없이 결과를 반환해야 한다.
            result = await kill_process_group(proc, metrics=sink)
        assert result.terminated is True

    @pytest.mark.asyncio
    async def test_sigterm_pgrp_already_gone(self):
        """SIGTERM 단계에서 그룹이 이미 사라진 경우 좀비만 회수한다."""
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 12345

        with patch("os.getpgid", return_value=12345), \
             patch("os.killpg", side_effect=ProcessLookupError), \
             patch("simpleclaw.security.process._reap_zombies", return_value=1):
            result = await kill_process_group(proc)

        assert result.terminated is True
        assert result.killed is False
        assert result.group_alive is False
        assert result.reaped_zombies == 1


class TestIsGroupAlive:
    def test_returns_false_when_killpg_raises_process_lookup(self):
        with patch("os.killpg", side_effect=ProcessLookupError):
            assert _is_group_alive(99999) is False

    def test_returns_true_when_killpg_succeeds(self):
        with patch("os.killpg", return_value=None):
            assert _is_group_alive(12345) is True

    def test_permission_error_treated_as_alive(self):
        """권한 오류는 프로세스가 존재한다는 신호이므로 잔존으로 간주."""
        with patch("os.killpg", side_effect=PermissionError):
            assert _is_group_alive(12345) is True


class TestReapZombies:
    def test_no_children_returns_zero(self):
        if sys.platform == "win32":
            pytest.skip("Unix-only test")
        with patch("os.waitpid", side_effect=ChildProcessError):
            assert _reap_zombies() == 0

    def test_counts_reaped_children(self):
        if sys.platform == "win32":
            pytest.skip("Unix-only test")
        # 두 자식 회수 후 더 이상 없는 시나리오.
        side_effects = [(1234, 0), (1235, 0), ChildProcessError()]
        with patch("os.waitpid", side_effect=side_effects):
            assert _reap_zombies() == 2

    def test_breaks_when_waitpid_returns_zero(self):
        """``waitpid``가 (0, 0)을 반환하면 더 이상 회수할 좀비가 없으므로 종료."""
        if sys.platform == "win32":
            pytest.skip("Unix-only test")
        with patch("os.waitpid", return_value=(0, 0)):
            assert _reap_zombies() == 0
