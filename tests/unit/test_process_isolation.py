"""Tests for process group isolation utilities."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.security.process import get_preexec_fn, kill_process_group


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
        await kill_process_group(proc)
        # Should not attempt to kill

    @pytest.mark.asyncio
    async def test_handles_process_lookup_error(self):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 99999

        with patch("os.getpgid", side_effect=ProcessLookupError):
            await kill_process_group(proc)
        # Should not raise

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
             patch("os.killpg") as mock_killpg:
            await kill_process_group(proc, timeout=1.0)

        # Should have sent SIGTERM
        mock_killpg.assert_called_once_with(12345, __import__("signal").SIGTERM)

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
             patch("os.killpg") as mock_killpg:
            await kill_process_group(proc, timeout=0.1)

        # Should have sent both SIGTERM and SIGKILL
        import signal
        calls = mock_killpg.call_args_list
        assert len(calls) == 2
        assert calls[0].args == (12345, signal.SIGTERM)
        assert calls[1].args == (12345, signal.SIGKILL)
