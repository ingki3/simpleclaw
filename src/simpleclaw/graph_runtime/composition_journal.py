"""Accepted final artifact를 SQLite에 durable write-once로 보존한다."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import weakref
from pathlib import Path
from typing import Protocol

from .contracts import FinalArtifactV1
from .idempotency import validate_canonical_artifact_identity


class FinalArtifactInvariantError(ValueError):
    """같은 request의 payload/composer continuity 충돌을 표시한다."""


class FinalArtifactJournal(Protocol):
    """FinalCompositionRuntime이 요구하는 durable journal 표면."""

    async def load(
        self,
        *,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
    ) -> FinalArtifactV1 | None: ...

    async def record_or_reuse(
        self,
        *,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
        artifact: FinalArtifactV1,
    ) -> FinalArtifactV1: ...

    def lock_for(self, request_id: str) -> asyncio.Lock: ...


_SCHEMA_LOCKS_GUARD = threading.Lock()
_SCHEMA_LOCKS: dict[str, threading.Lock] = {}
_LOOP_LOCKS_GUARD = threading.Lock()
_LOOP_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[tuple[str, str], asyncio.Lock],
] = weakref.WeakKeyDictionary()


def _schema_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _SCHEMA_LOCKS_GUARD:
        return _SCHEMA_LOCKS.setdefault(key, threading.Lock())


class SQLiteFinalArtifactJournal:
    """매 연산의 새 connection을 worker thread에서 열어 event loop를 보호한다."""

    def __init__(self, db_path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        self._path = Path(db_path).expanduser()
        self._timeout_seconds = max(float(timeout_seconds), 0.1)
        self._schema_lock = _schema_lock(self._path)
        self._initialized = False

    def lock_for(self, request_id: str) -> asyncio.Lock:
        """같은 loop/path/request의 모든 runtime 인스턴스가 공유하는 compose lock."""
        loop = asyncio.get_running_loop()
        key = (str(self._path.resolve()), request_id)
        with _LOOP_LOCKS_GUARD:
            locks = _LOOP_LOCKS.setdefault(loop, {})
            return locks.setdefault(key, asyncio.Lock())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={int(self._timeout_seconds * 1000)}")
        return connection

    def _ensure_schema_sync(self) -> None:
        if self._initialized:
            return
        with self._schema_lock:
            if self._initialized:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_final_artifacts (
                        request_id TEXT PRIMARY KEY,
                        normalized_payload_hash TEXT NOT NULL,
                        composer_fingerprint TEXT NOT NULL,
                        artifact_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
            finally:
                connection.close()
            self._initialized = True

    @staticmethod
    def _validate_artifact(
        artifact: FinalArtifactV1,
        *,
        request_id: str,
    ) -> FinalArtifactV1:
        if artifact.request_id != request_id:
            raise FinalArtifactInvariantError("final artifact request_id mismatch")
        validate_canonical_artifact_identity(
            request_id=artifact.request_id,
            content=artifact.content,
            artifact_id=artifact.artifact_id,
            content_hash=artifact.content_hash,
        )
        return artifact

    @classmethod
    def _decode_row(
        cls,
        row: tuple[str, str, str],
        *,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
    ) -> FinalArtifactV1:
        stored_payload_hash, stored_fingerprint, artifact_json = row
        if stored_payload_hash != normalized_payload_hash:
            raise FinalArtifactInvariantError(
                "final artifact request reused with different normalized payload"
            )
        if stored_fingerprint != composer_fingerprint:
            raise FinalArtifactInvariantError(
                "final artifact request reused with different composer fingerprint"
            )
        artifact = FinalArtifactV1.model_validate_json(artifact_json)
        return cls._validate_artifact(artifact, request_id=request_id)

    def _load_sync(
        self,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
    ) -> FinalArtifactV1 | None:
        self._ensure_schema_sync()
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT normalized_payload_hash, composer_fingerprint, artifact_json
                FROM graph_final_artifacts WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return self._decode_row(
            row,
            request_id=request_id,
            normalized_payload_hash=normalized_payload_hash,
            composer_fingerprint=composer_fingerprint,
        )

    async def load(
        self,
        *,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
    ) -> FinalArtifactV1 | None:
        """Identity continuity를 검증한 기존 final을 event loop 밖에서 읽는다."""
        return await asyncio.to_thread(
            self._load_sync,
            request_id,
            normalized_payload_hash,
            composer_fingerprint,
        )

    def _record_sync(
        self,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
        artifact: FinalArtifactV1,
    ) -> FinalArtifactV1:
        self._ensure_schema_sync()
        self._validate_artifact(artifact, request_id=request_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO graph_final_artifacts (
                    request_id, normalized_payload_hash, composer_fingerprint,
                    artifact_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO NOTHING
                """,
                (
                    request_id,
                    normalized_payload_hash,
                    composer_fingerprint,
                    artifact.model_dump_json(by_alias=True),
                    time.time(),
                ),
            )
            row = connection.execute(
                """
                SELECT normalized_payload_hash, composer_fingerprint, artifact_json
                FROM graph_final_artifacts WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        if row is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("final artifact journal write produced no row")
        return self._decode_row(
            row,
            request_id=request_id,
            normalized_payload_hash=normalized_payload_hash,
            composer_fingerprint=composer_fingerprint,
        )

    async def record_or_reuse(
        self,
        *,
        request_id: str,
        normalized_payload_hash: str,
        composer_fingerprint: str,
        artifact: FinalArtifactV1,
    ) -> FinalArtifactV1:
        """첫 artifact만 commit하고 경쟁자는 durable 최초 값을 재사용한다."""
        return await asyncio.to_thread(
            self._record_sync,
            request_id,
            normalized_payload_hash,
            composer_fingerprint,
            artifact,
        )
