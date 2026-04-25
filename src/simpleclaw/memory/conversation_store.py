"""대화 이력을 SQLite 데이터베이스에 저장하고 조회하는 모듈.

주요 동작 흐름:
1. ConversationStore 인스턴스 생성 시 SQLite DB 파일 경로를 받아 스키마를 자동 생성한다.
2. add_message()로 대화 메시지를 순차적으로 저장한다.
3. get_recent() / get_since()로 최근 대화 또는 특정 시점 이후 대화를 조회한다.

설계 결정:
- 각 메서드 호출마다 sqlite3.connect()를 사용하여 연결을 열고 닫는다.
  장기 실행 프로세스에서 파일 잠금 문제를 방지하기 위함이다.
- 메시지 순서는 auto-increment id 기준이며, timestamp는 보조 필터로만 사용한다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.models import ConversationMessage, MessageRole

logger = logging.getLogger(__name__)


class ConversationStore:
    """대화 이력을 로컬 SQLite 데이터베이스에 저장하고 조회하는 저장소.

    인스턴스 생성 시 DB 파일이 없으면 자동으로 생성하며,
    messages 테이블 스키마도 함께 초기화한다.
    """

    def __init__(self, db_path: str | Path) -> None:
        """대화 저장소를 초기화한다.

        Args:
            db_path: SQLite 데이터베이스 파일 경로. 존재하지 않으면 새로 생성된다.
        """
        self._db_path = str(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """messages 테이블이 존재하지 않으면 생성한다. DB 파일의 부모 디렉터리도 함께 생성."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0
                )
            """)

    def add_message(self, message: ConversationMessage) -> None:
        """새 대화 메시지를 DB에 저장한다.

        Args:
            message: 저장할 대화 메시지 객체. role, content, timestamp, token_count를 포함한다.
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO messages (role, content, timestamp, token_count) VALUES (?, ?, ?, ?)",
                (
                    message.role.value,
                    message.content,
                    message.timestamp.isoformat(),
                    message.token_count,
                ),
            )

    def get_recent(self, limit: int = 20) -> list[ConversationMessage]:
        """최근 메시지를 시간순으로 반환한다.

        Args:
            limit: 가져올 최대 메시지 수. 기본값 20.

        Returns:
            시간순(오래된 것 먼저)으로 정렬된 ConversationMessage 리스트.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count FROM messages "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        messages = []
        # DB에서 역순(최신 먼저)으로 가져온 뒤 다시 뒤집어 시간순으로 만든다
        for role, content, ts, tokens in reversed(rows):
            messages.append(ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            ))
        return messages

    def get_since(self, since: datetime) -> list[ConversationMessage]:
        """지정된 시점 이후의 메시지를 시간순으로 반환한다.

        Args:
            since: 이 시점 이후(포함)의 메시지를 조회한다.

        Returns:
            시간순으로 정렬된 ConversationMessage 리스트.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count FROM messages "
                "WHERE timestamp >= ? ORDER BY id",
                (since.isoformat(),),
            ).fetchall()

        return [
            ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            )
            for role, content, ts, tokens in rows
        ]

    def count(self) -> int:
        """저장된 전체 메시지 수를 반환한다."""
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
