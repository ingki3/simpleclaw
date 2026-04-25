"""Process group isolation utilities.

Provides helpers to spawn subprocesses in their own process group
and kill the entire group on timeout, preventing zombie processes.
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
    """Return ``os.setsid`` on Unix platforms, ``None`` on Windows."""
    if sys.platform == "win32":
        return None
    return os.setsid


async def kill_process_group(
    process: asyncio.subprocess.Process,
    timeout: float = 5.0,
) -> None:
    """Kill the entire process group of *process*.

    Sends SIGTERM first, waits up to *timeout* seconds, then SIGKILL.
    Silently handles already-dead processes.
    """
    if process.returncode is not None:
        return  # Already finished

    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return  # Process already gone

    # SIGTERM the group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    # Wait briefly for graceful exit
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        pass

    # SIGKILL the group
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass

    try:
        await process.wait()
    except Exception:
        pass

    logger.warning("Force-killed process group pgid=%d (pid=%d)", pgid, pid)
