"""Conversation history storage using SQLite."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.models import ConversationMessage, MessageRole

logger = logging.getLogger(__name__)


class ConversationStore:
    """Stores conversation history in a local SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
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
        """Store a new conversation message."""
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
        """Retrieve the most recent messages."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, token_count FROM messages "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        messages = []
        for role, content, ts, tokens in reversed(rows):
            messages.append(ConversationMessage(
                role=MessageRole(role),
                content=content,
                timestamp=datetime.fromisoformat(ts),
                token_count=tokens,
            ))
        return messages

    def get_since(self, since: datetime) -> list[ConversationMessage]:
        """Retrieve messages since a given timestamp."""
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
        """Return total message count."""
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
