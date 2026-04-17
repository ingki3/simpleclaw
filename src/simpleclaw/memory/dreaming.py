"""Dreaming pipeline: summarize conversations and update core memory."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.models import DreamingError, MemoryEntry
from simpleclaw.memory.conversation_store import ConversationStore

logger = logging.getLogger(__name__)


class DreamingPipeline:
    """Processes conversation history into core memory summaries.

    Creates .bak backups before modifying persona files to allow
    rollback in case of hallucination or corruption.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_file: str | Path,
    ) -> None:
        self._store = conversation_store
        self._memory_file = Path(memory_file)

    def create_backup(self) -> Path | None:
        """Create a .bak backup of the memory file before modification.

        Returns the backup path, or None if the file doesn't exist.
        """
        if not self._memory_file.is_file():
            return None

        backup_path = self._memory_file.with_suffix(
            f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        )
        shutil.copy2(self._memory_file, backup_path)
        logger.info("Created memory backup: %s", backup_path)
        return backup_path

    def collect_unprocessed(self, last_dreaming: datetime | None = None) -> list:
        """Collect conversation messages since last dreaming session."""
        if last_dreaming:
            return self._store.get_since(last_dreaming)
        return self._store.get_recent(limit=50)

    def summarize(self, messages: list) -> str:
        """Generate a summary of conversation messages.

        Note: In production, this would call an LLM to generate the
        summary. For now, it produces a simple text-based summary.
        """
        if not messages:
            return ""

        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        lines.append(f"## Session {date_str}")
        lines.append("")

        topics = set()
        for msg in messages:
            # Extract key phrases (simple heuristic)
            words = msg.content.split()[:10]
            if words:
                topics.add(" ".join(words[:5]))

        for topic in list(topics)[:5]:
            lines.append(f"- {topic}...")

        return "\n".join(lines)

    def append_to_memory(self, summary: str) -> None:
        """Append a dreaming summary to MEMORY.md."""
        if not summary:
            return

        self._memory_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if self._memory_file.is_file():
            existing = self._memory_file.read_text(encoding="utf-8")

        if existing and not existing.endswith("\n"):
            existing += "\n"

        new_content = f"{existing}\n{summary}\n"
        self._memory_file.write_text(new_content, encoding="utf-8")
        logger.info("Updated memory file: %s", self._memory_file)

    async def run(self, last_dreaming: datetime | None = None) -> MemoryEntry | None:
        """Execute the full dreaming pipeline.

        1. Create backup of MEMORY.md
        2. Collect unprocessed messages
        3. Generate summary
        4. Append to MEMORY.md
        """
        # Step 1: Backup
        self.create_backup()

        # Step 2: Collect
        messages = self.collect_unprocessed(last_dreaming)
        if not messages:
            logger.info("No new messages to process for dreaming.")
            return None

        # Step 3: Summarize
        summary = self.summarize(messages)
        if not summary:
            return None

        # Step 4: Append
        self.append_to_memory(summary)

        return MemoryEntry(
            summary=summary,
            source=f"dreaming_{datetime.now().strftime('%Y-%m-%d')}",
        )
