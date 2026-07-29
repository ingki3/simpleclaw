"""BIZ-512 live DB read-only 추출·redaction·split 계약."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from simpleclaw.evaluation.functiongemma_dataset import (
    SanitizedCase,
    assign_splits,
    ensure_private_output_dir,
    extract_cases,
    opaque_run_id,
    redact_text,
    write_private_json,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, "
            "content TEXT, timestamp TEXT, channel TEXT, deleted_at TEXT)"
        )
        rows = [
            (1, "user", "메일 me@example.com token=secret123", "t1", "telegram", None),
            (2, "assistant", "확인했습니다", "t2", "telegram", None),
            (3, "user", "https://example.com/private/path 확인", "t3", "cron", None),
            (4, "user", "삭제됨", "t4", "telegram", "now"),
        ]
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", rows)


def test_read_only_extract_redacts_and_preserves_row_count(tmp_path: Path) -> None:
    db = tmp_path / "conversations.db"
    _database(db)
    result = extract_cases(db)
    assert result.row_count_before == result.row_count_after == 3
    assert result.source_scan_count == 2
    assert len(result.cases) == 2
    assert "<email>" in result.cases[0].current
    assert "<credential>" in result.cases[0].current
    assert "private/path" not in result.cases[1].current
    assert result.cases[1].channel_stratum == "cron"
    rendered = str(result.cases)
    assert "msg:1" not in rendered
    assert "live:1" not in rendered
    assert all(
        message.id.startswith("opaque-turn-")
        for case in result.cases
        for message in case.history
    )
    assert all(case.case_id.startswith("opaque-case-") for case in result.cases)


def test_redaction_covers_phone_ids_private_path_and_credentials() -> None:
    raw = (
        '010-1234-5678 user_id=abc "chatId": 101 message id=xyz '
        "/Users/alice/private "
        "Authorization: Bearer-very-secret"
    )
    clean = redact_text(raw)
    assert "010-1234-5678" not in clean
    assert "user_id=abc" not in clean
    assert "chatId" not in clean
    assert "message id" not in clean
    assert "/Users/alice" not in clean
    assert "Bearer-very-secret" not in clean


def test_opaque_ids_are_deterministic_and_ignore_raw_db_ids(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    _database(first)
    _database(second)
    with sqlite3.connect(second) as conn:
        conn.execute("UPDATE messages SET id = id + 100")

    first_cases = extract_cases(first, run_seed=7).cases
    second_cases = extract_cases(second, run_seed=7).cases

    assert [case.case_id for case in first_cases] == [
        case.case_id for case in second_cases
    ]
    assert [
        message.id for case in first_cases for message in case.history
    ] == [
        message.id for case in second_cases for message in case.history
    ]
    assert opaque_run_id(seed=7, namespace="case", ordinal=0) != (
        opaque_run_id(seed=8, namespace="case", ordinal=0)
    )


def test_split_is_stable_by_source_group_without_leakage() -> None:
    base = SanitizedCase("a", "group", (), "x", "telegram", "fp")
    sibling = SanitizedCase("b", "group", (), "y", "telegram", "fp2")
    other = SanitizedCase("c", "other", (), "z", "telegram", "fp3")
    first = assign_splits((base, sibling, other))
    second = assign_splits((base, sibling, other))
    assert [item.split for item in first] == [item.split for item in second]
    assert first[0].split == first[1].split


def test_private_permissions_and_case_cap(tmp_path: Path) -> None:
    private = ensure_private_output_dir(tmp_path / "private")
    artifact = write_private_json(private / "manifest.json", {"count": 1})
    assert os.stat(private).st_mode & 0o777 == 0o700
    assert os.stat(artifact).st_mode & 0o777 == 0o600
    db = tmp_path / "conversations.db"
    _database(db)
    with pytest.raises(ValueError, match="between"):
        extract_cases(db, max_cases=301)
