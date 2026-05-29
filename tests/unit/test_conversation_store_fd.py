"""ConversationStore SQLite 파일 디스크립터 누수 회귀 테스트."""

from __future__ import annotations

import gc
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import (
    ConversationMessage,
    MemoryItemType,
    MessageRole,
)


ITERATIONS = 100


def _is_sqlite_fd_target(target: str, db_path: Path) -> bool:
    """FD target이 테스트 DB 또는 WAL/SHM sidecar를 가리키는지 판별한다."""
    target = target.removesuffix(" (deleted)")
    db = str(db_path.resolve())
    return target == db or target.startswith(f"{db}-")


def _count_open_sqlite_fds(db_path: Path) -> int:
    """현재 프로세스가 열어 둔 테스트 SQLite DB 관련 FD 수를 반환한다.

    Linux CI에서는 ``/proc/self/fd``를 직접 읽고, macOS 로컬 검증에서는 ``lsof``를
    사용한다. 둘 다 불가능한 환경은 FD 누수 여부를 관측할 수 없으므로 skip한다.
    """
    proc_fd = Path("/proc/self/fd")
    if proc_fd.is_dir():
        count = 0
        for fd in proc_fd.iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if _is_sqlite_fd_target(target, db_path):
                count += 1
        return count

    if shutil.which("lsof"):
        result = subprocess.run(
            ["lsof", "-Fn", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode not in (0, 1):
            pytest.skip(f"lsof failed: {result.stderr.strip()}")
        return sum(
            1
            for line in result.stdout.splitlines()
            if line.startswith("n") and _is_sqlite_fd_target(line[1:], db_path)
        )

    pytest.skip("open FD inspection requires /proc/self/fd or lsof")


def _assert_no_sqlite_fd_growth(db_path: Path, operation: Callable[[], None]) -> None:
    """반복 DB operation 이후 SQLite FD 수가 증가하지 않음을 검증한다."""
    gc.collect()
    before = _count_open_sqlite_fds(db_path)
    for _ in range(ITERATIONS):
        operation()
    gc.collect()
    after = _count_open_sqlite_fds(db_path)
    assert after <= before, (
        f"SQLite FD count grew from {before} to {after} for {db_path}"
    )


def test_get_recent_does_not_leak_sqlite_fds(tmp_path: Path) -> None:
    """get_recent 반복 조회가 conversations.db FD를 누적하지 않는다."""
    db_path = tmp_path / "conversations.db"
    store = ConversationStore(db_path)
    store.add_message(ConversationMessage(role=MessageRole.USER, content="hello"))

    _assert_no_sqlite_fd_growth(db_path, lambda: store.get_recent(limit=1))


def test_add_message_does_not_leak_sqlite_fds(tmp_path: Path) -> None:
    """add_message 반복 저장이 쓰기 connection FD를 누적하지 않는다."""
    db_path = tmp_path / "conversations.db"
    store = ConversationStore(db_path)
    counter = 0

    def add_message() -> None:
        nonlocal counter
        counter += 1
        store.add_message(
            ConversationMessage(
                role=MessageRole.USER,
                content=f"message {counter}",
            )
        )

    _assert_no_sqlite_fd_growth(db_path, add_message)


def test_memory_item_queries_close_connections(tmp_path: Path) -> None:
    """BIZ-307 장기기억 조회/검색 메서드도 SQLite FD를 닫는다."""
    db_path = tmp_path / "conversations.db"
    store = ConversationStore(db_path)
    item = store.create_memory_item(
        item_type=MemoryItemType.MEMORY,
        text="FD leak regression memory",
        source="test",
        source_ref="fd-leak",
        confidence=0.9,
        embedding=[1.0, 0.0, 0.0],
    )

    def query_memory_items() -> None:
        assert store.get_memory_item(item.id) is not None
        assert store.list_memory_items(limit=5)
        assert store.search_memory_items([1.0, 0.0, 0.0], k=1)

    _assert_no_sqlite_fd_growth(db_path, query_memory_items)
