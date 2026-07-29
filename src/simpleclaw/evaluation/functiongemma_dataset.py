"""live conversations.db를 read-only로 축소·비식별화하는 PoC 추출기."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE_CASES = 300
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

_REDACTIONS = (
    ("credential", re.compile(
        r"(?i)\b(?:api[_-]?key|token|secret|password|authorization)\b"
        r"\s*[:=]\s*['\"]?[^\s,;'\"`]+"
    )),
    ("credential", re.compile(
        r"(?i)\b(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
        r"(?:sk|gh[pousr])[-_][A-Za-z0-9._-]{8,})\b"
    )),
    ("email", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")),
    ("url", re.compile(r"(?i)\bhttps?://[^\s/]+(?:/[^\s?#]*)?(?:\?[^\s#]*)?")),
    ("private_path", re.compile(r"(?<![\w:/])/(?:Users|home|private|tmp)/[^\s]+")),
    ("identifier", re.compile(
        r"(?i)\b(?:user|chat|message|msg)[_-]?id\s*[:=]\s*[A-Za-z0-9_-]+"
    )),
)


@dataclass(frozen=True)
class SanitizedMessage:
    id: str
    role: str
    content: str


@dataclass(frozen=True)
class SanitizedCase:
    case_id: str
    source_group_id: str
    history: tuple[SanitizedMessage, ...]
    current: str
    channel_stratum: str
    source_fingerprint: str
    split: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    cases: tuple[SanitizedCase, ...]
    row_count_before: int
    row_count_after: int
    source_scan_count: int
    db_fingerprint: str


def redact_text(text: str) -> str:
    """민감 패턴을 값 복원이 불가능한 종류별 placeholder로 치환한다."""
    redacted = str(text or "")
    for label, pattern in _REDACTIONS:
        redacted = pattern.sub(f"<{label}>", redacted)
    return redacted


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def count_live_rows(path: Path) -> int:
    with _connect_read_only(path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM messages WHERE deleted_at IS NULL"
        ).fetchone()[0])


def _stratum(channel: object, content: str) -> str:
    channel_text = str(channel or "unknown").lower()
    if channel_text in {"cron", "recipe", "system"}:
        return channel_text
    lowered = content.lower()
    if lowered.startswith(("[cron]", "cron:")):
        return "cron"
    if lowered.startswith(("[recipe]", "recipe:")):
        return "recipe"
    if lowered.startswith(("[system]", "system:")):
        return "system_generated"
    return channel_text


def extract_cases(
    live_db: str | Path,
    *,
    max_cases: int = MAX_SOURCE_CASES,
    history_messages: int = 4,
    excluded_fingerprints: Iterable[str] = (),
) -> ExtractionResult:
    """non-deleted user turn을 읽고 bounded history와 함께 최대 300건 반환한다."""
    if not 1 <= max_cases <= MAX_SOURCE_CASES:
        raise ValueError(f"max_cases must be between 1 and {MAX_SOURCE_CASES}")
    db_path = Path(live_db).expanduser()
    before = count_live_rows(db_path)
    excluded = set(excluded_fingerprints)
    with _connect_read_only(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        channel_expr = "channel" if "channel" in columns else "NULL"
        rows = conn.execute(
            "SELECT id, role, content, "
            f"{channel_expr} FROM messages WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
    after = count_live_rows(db_path)
    if before != after:
        raise RuntimeError("live row count changed during read-only extraction")

    sanitized: list[SanitizedMessage] = []
    cases: list[SanitizedCase] = []
    user_scan_count = 0
    for row_id, role, raw_content, channel in rows:
        clean = redact_text(raw_content)
        message = SanitizedMessage(
            id=f"msg:{row_id}", role=str(role), content=clean
        )
        if role == "user":
            user_scan_count += 1
            fingerprint = text_fingerprint(clean)
            if fingerprint not in excluded and len(cases) < max_cases:
                source_id = f"live:{row_id}"
                source_group_id = f"source:{fingerprint[:24]}"
                cases.append(SanitizedCase(
                    case_id=source_id,
                    source_group_id=source_group_id,
                    history=tuple(sanitized[-history_messages:]),
                    current=clean,
                    channel_stratum=_stratum(channel, clean),
                    source_fingerprint=fingerprint,
                ))
        sanitized.append(message)

    db_stat = db_path.stat()
    db_fingerprint = text_fingerprint(
        f"{db_path.name}:{db_stat.st_size}:{before}"
    )
    return ExtractionResult(
        cases=tuple(cases),
        row_count_before=before,
        row_count_after=after,
        source_scan_count=user_scan_count,
        db_fingerprint=db_fingerprint,
    )


def assign_splits(
    cases: Sequence[SanitizedCase],
    *,
    seed: int = 42,
    train_ratio: float = 0.8,
    dev_ratio: float = 0.1,
) -> tuple[SanitizedCase, ...]:
    """source_group ID를 먼저 hash해 augmentation 이전 split을 고정한다."""
    if train_ratio <= 0 or dev_ratio < 0 or train_ratio + dev_ratio >= 1:
        raise ValueError("invalid split ratios")
    assigned: list[SanitizedCase] = []
    group_splits: dict[str, str] = {}
    for case in cases:
        split = group_splits.get(case.source_group_id)
        if split is None:
            digest = hashlib.sha256(
                f"{seed}:{case.source_group_id}".encode()
            ).digest()
            point = int.from_bytes(digest[:8], "big") / 2**64
            split = (
                "train" if point < train_ratio
                else "dev" if point < train_ratio + dev_ratio
                else "test"
            )
            group_splits[case.source_group_id] = split
        assigned.append(SanitizedCase(**{
            **case.to_dict(),
            "history": case.history,
            "split": split,
        }))
    return tuple(assigned)


def ensure_private_output_dir(path: str | Path) -> Path:
    output = Path(path).expanduser()
    output.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    os.chmod(output, PRIVATE_DIR_MODE)
    if output.stat().st_mode & 0o077:
        raise PermissionError("private output directory must be mode 0700")
    return output


def write_private_json(path: str | Path, value: object) -> Path:
    target = Path(path)
    ensure_private_output_dir(target.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(target, flags, PRIVATE_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    os.chmod(target, PRIVATE_FILE_MODE)
    return target


def write_private_jsonl(path: str | Path, rows: Iterable[object]) -> Path:
    target = Path(path)
    ensure_private_output_dir(target.parent)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        for row in rows:
            payload = row.to_dict() if hasattr(row, "to_dict") else row
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    os.chmod(target, PRIVATE_FILE_MODE)
    return target
